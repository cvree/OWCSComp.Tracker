"""
reconcile.py — does a spreadsheet map describe a VOD-verified map?

Conservative by construction:

  * A candidate must have the SAME two teams (resolved ids). Nothing else can
    make two maps the same map.
  * The score adds evidence: date (same day / ±1 day for time-zone edges),
    map name, map order, event/stage wording, and the VOD link when the sheet
    has one. A disagreeing map name or order SUBTRACTS.
  * Auto-link only when the best candidate clears AUTO_LINK_SCORE, beats the
    runner-up by AUTO_LINK_MARGIN, and its map name does not disagree.
    Anything weaker goes to the review queue (`needs_review`) with every
    candidate and the reasons for its score; nothing uncertain is merged.
  * A link never overwrites anything. Both records are kept and compared
    field by field; each field is `agree`, `disagree` or `unknown` (one side
    did not record it). The record's status follows from those:

        matched       every field both sides recorded agrees
        partial       nothing disagrees, but some field is recorded by one side only
        conflict      at least one field disagrees — both claims are shown
        unmatched     no candidate (or the human said none of them)
        needs_review  candidates exist but none is safe to link automatically

Manual decisions (corrections/external_reconciliation.json) override the
matcher and are recorded on the record they decided.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .aliases import norm

AUTO_LINK_SCORE = 9.0
AUTO_LINK_MARGIN = 3.0
REVIEW_SCORE = 5.0
#: A hero the VOD saw for less than this many live seconds is "brief": a
#: sheet that did not list it is not contradicting the VOD.
BRIEF_SECONDS = 30
STATUSES = ("matched", "partial", "conflict", "unmatched", "needs_review")


def _vid(url: str | None) -> str | None:
    if not url:
        return None
    for rx in (r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([A-Za-z0-9_-]{11})",
               r"twitch\.tv/videos/(\d+)"):
        m = re.search(rx, url)
        if m:
            return m.group(1)
    return None


def _days(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    try:
        return abs((dt.date.fromisoformat(a[:10]) - dt.date.fromisoformat(b[:10])).days)
    except ValueError:
        return None


def vod_maps(public: dict) -> list[dict]:
    """The published VOD maps, flattened with what reconciliation compares."""
    tw = {m["mapId"]: m for m in ((public.get("timeWeighting") or {}).get("maps") or [])}
    stints = public.get("heroStints") or []
    tours = {t["id"]: t for t in public.get("tournaments") or []}
    out = []
    for match in public.get("matches") or []:
        tour = tours.get(match.get("tournamentId")) or {}
        for mp in match.get("maps") or []:
            heroes: dict[str, dict[str, int]] = {}
            t = tw.get(mp["id"])
            if t:
                for team in t["teams"]:
                    heroes[team["teamId"]] = {h["hero"]: h["seconds"] for h in team["heroes"]}
            else:
                for s in stints:
                    if s.get("mapId") == mp["id"]:
                        heroes.setdefault(s["teamId"], {})[s["hero"]] = BRIEF_SECONDS
            bans = sorted(b["hero"] for b in public.get("heroBans") or [] if b.get("mapId") == mp["id"])
            out.append({
                "vodMapId": mp["id"], "matchId": match["id"],
                "date": (match.get("scheduledAt") or "")[:10] or None,
                "teamA": match.get("teamA"), "teamB": match.get("teamB"),
                "map": mp.get("map"), "order": mp.get("order"), "winner": mp.get("winner"),
                "pickedBy": mp.get("pickedBy"), "mode": mp.get("mode"),
                "event": tour.get("name"), "stage": ((tour.get("stages") or [{}])[0] or {}).get("name"),
                "vodUrl": match.get("streamUrl"), "heroes": heroes, "bans": bans,
                "hasTimeline": bool(t), "coverage": t.get("coverage") if t else None,
            })
    return out


def score(ext: dict, match: dict, vod: dict) -> tuple[float, list[str], bool]:
    """(score, reasons, map-disagrees). -inf when the teams differ."""
    teams_ext = {ext.get("teamA"), ext.get("teamB")}
    teams_vod = {vod.get("teamA"), vod.get("teamB")}
    if None in teams_ext or teams_ext != teams_vod:
        return float("-inf"), ["different teams"], False
    s, why, map_conflict = 4.0, ["same two teams (+4)"], False
    d = _days(ext.get("date"), vod.get("date"))
    if d is None:
        why.append("one side has no date (+0)")
    elif d == 0:
        s += 3; why.append("same date (+3)")
    elif d == 1:
        s += 2; why.append("dates one day apart — time-zone edge (+2)")
    elif d <= 3:
        s += 0.5; why.append(f"dates {d} days apart (+0.5)")
    else:
        return float("-inf"), [f"dates {d} days apart"], False
    if ext.get("map") and vod.get("map"):
        if ext["map"] == vod["map"]:
            s += 3; why.append("same map (+3)")
        else:
            s -= 3; map_conflict = True; why.append(f"different map: sheet {ext['map']} vs VOD {vod['map']} (−3)")
    if ext.get("order") is not None and vod.get("order") is not None:
        if ext["order"] == vod["order"]:
            s += 2; why.append("same map number (+2)")
        else:
            s -= 1; why.append(f"different map number: sheet {ext['order']} vs VOD {vod['order']} (−1)")
    ev = set(norm(match.get("event")).split()) | set(norm(match.get("stageRaw")).split())
    vv = set(norm(vod.get("event")).split()) | set(norm(vod.get("stage")).split())
    common = (ev & vv) - {"owcs", "the", "of", "and", "2024", "2025", "2026"}
    if common:
        s += 1; why.append(f"event wording overlaps: {', '.join(sorted(common))} (+1)")
    ev_vid, vod_vid = _vid(ext.get("vodUrl")), _vid(vod.get("vodUrl"))
    if ev_vid and vod_vid:
        if ev_vid == vod_vid:
            s += 3; why.append("same VOD video (+3)")
        else:
            s -= 2; why.append("different VOD video (−2)")
    return s, why, map_conflict


def _field(sheet: Any, vod: Any, note: str | None = None) -> dict:
    if sheet in (None, "", []) or vod in (None, "", []):
        agree = None
    else:
        agree = sheet == vod
    return {"sheet": sheet, "vod": vod, "agree": agree, "note": note}


def compare(ext: dict, match: dict, vod: dict, ext_bans: list[dict], ext_apps: list[dict],
            ext_comps: list[dict]) -> dict:
    """Field-by-field comparison of a linked pair. Nothing is chosen."""
    fields: dict[str, dict] = {}
    d = _days(ext.get("date"), vod.get("date"))
    fields["date"] = _field(ext.get("date"), vod.get("date"))
    if d == 1:
        fields["date"].update(agree=True, note="one day apart — treated as the same day (time zones)")
    fields["map"] = _field(ext.get("map"), vod.get("map"))
    fields["order"] = _field(ext.get("order"), vod.get("order"))
    fields["mode"] = _field(ext.get("mode"), vod.get("mode") if vod.get("mode") not in ("Unknown",) else None)
    fields["winner"] = _field(ext.get("winner"), vod.get("winner"))
    fields["pickedBy"] = _field(ext.get("pickedBy"), vod.get("pickedBy"))
    sb = sorted(b["hero"] for b in ext_bans if b.get("hero"))
    fields["bans"] = _field(sb or None, vod.get("bans") or None)
    for team in sorted({ext.get("teamA"), ext.get("teamB")} - {None}):
        listed = sorted({a["hero"] for a in ext_apps if a.get("teamId") == team and a.get("hero")} |
                        {h for c in ext_comps if c.get("teamId") == team for h in c["heroes"]})
        seen = vod["heroes"].get(team) or {}
        if not listed or not seen:
            fields[f"heroes:{team}"] = {"sheet": listed or None, "vod": sorted(seen) or None,
                                        "agree": None, "note": None,
                                        "sheetOnly": [], "vodOnly": [], "vodOnlyBrief": []}
            continue
        sheet_only = [h for h in listed if h not in seen]
        vod_only = [h for h in seen if h not in listed and seen[h] >= BRIEF_SECONDS]
        brief = [h for h in seen if h not in listed and seen[h] < BRIEF_SECONDS]
        if sheet_only:
            agree, note = False, "the sheet lists a hero the VOD timeline never shows on this team"
        elif vod_only:
            agree, note = None, "the VOD shows a hero for 30 s or more that the sheet does not list"
        else:
            agree, note = True, ("every listed hero was seen on the VOD" +
                                 (f"; briefly seen, not listed: {', '.join(brief)}" if brief else ""))
        fields[f"heroes:{team}"] = {"sheet": listed, "vod": sorted(seen), "agree": agree,
                                    "note": note, "sheetOnly": sheet_only, "vodOnly": vod_only,
                                    "vodOnlyBrief": brief}
    return fields


def status_of(fields: dict) -> str:
    vals = [f["agree"] for f in fields.values()]
    if any(v is False for v in vals):
        return "conflict"
    if any(v is None for v in vals):
        return "partial"
    return "matched"


def reconcile(normalized: dict, public: dict, decisions: dict) -> dict:
    vods = vod_maps(public)
    vod_by_id = {v["vodMapId"]: v for v in vods}
    matches = {m["id"]: m for m in normalized.get("matches") or []}
    bans_by, apps_by, comps_by = {}, {}, {}
    for b in normalized.get("bans") or []:
        bans_by.setdefault(b["mapId"], []).append(b)
    for a in normalized.get("appearances") or []:
        apps_by.setdefault(a["mapId"], []).append(a)
    for c in normalized.get("compositions") or []:
        comps_by.setdefault(c["mapId"], []).append(c)
    by_ext: dict[str, list[dict]] = {}
    for d in decisions.get("decisions") or []:
        if d.get("externalMapId"):
            by_ext.setdefault(d["externalMapId"], []).append(d)

    records = []
    linked_vod: dict[str, list[str]] = {}
    for ext in normalized.get("maps") or []:
        match = matches.get(ext["matchId"]) or {}
        mine = by_ext.get(ext["id"], [])
        rejected = {d.get("vodMapId") for d in mine if d.get("action") == "reject"}
        link = next((d for d in reversed(mine) if d.get("action") == "link"), None)
        ignore = next((d for d in reversed(mine) if d.get("action") == "ignore"), None)
        cands = []
        for v in vods:
            if v["vodMapId"] in rejected:
                continue
            sc, why, map_conflict = score(ext, match, v)
            if sc == float("-inf") or sc < REVIEW_SCORE:
                continue
            cands.append({"vodMapId": v["vodMapId"], "score": round(sc, 2), "reasons": why,
                          "mapDisagrees": map_conflict})
        cands.sort(key=lambda c: (-c["score"], c["vodMapId"]))
        rec = {"id": "rec-" + ext["id"][5:], "externalMapId": ext["id"], "vodMapId": None,
               "status": "unmatched", "method": None, "score": None,
               "candidates": cands[:5], "fields": {}, "decisions": mine, "ignored": bool(ignore),
               "reason": None}
        chosen = None
        if ignore:
            rec["reason"] = "ignored by a reviewer: " + (ignore.get("note") or "no note")
        elif link and link.get("vodMapId") in vod_by_id:
            chosen, rec["method"] = vod_by_id[link["vodMapId"]], "manual"
            rec["reason"] = "linked by a reviewer"
        elif link:
            rec["status"] = "needs_review"
            rec["reason"] = f"a reviewer linked VOD map {link.get('vodMapId')}, which is not in the published dataset"
        elif cands:
            best = cands[0]
            runner = cands[1]["score"] if len(cands) > 1 else float("-inf")
            if (best["score"] >= AUTO_LINK_SCORE and best["score"] - runner >= AUTO_LINK_MARGIN
                    and not best["mapDisagrees"]):
                chosen, rec["method"] = vod_by_id[best["vodMapId"]], "auto"
                rec["reason"] = f"score {best['score']} ≥ {AUTO_LINK_SCORE} and clear of the runner-up"
            else:
                rec["status"] = "needs_review"
                rec["reason"] = ("map name disagrees with the best candidate" if best["mapDisagrees"]
                                 else f"best score {best['score']} is below {AUTO_LINK_SCORE}"
                                 if best["score"] < AUTO_LINK_SCORE
                                 else "two candidates are too close to choose between")
        else:
            rec["reason"] = ("no published VOD map has these two teams on a nearby date"
                             if not rejected else "every candidate was rejected by a reviewer")
        if chosen:
            rec["vodMapId"] = chosen["vodMapId"]
            rec["score"] = next((c["score"] for c in cands if c["vodMapId"] == chosen["vodMapId"]), None)
            rec["fields"] = compare(ext, match, chosen, bans_by.get(ext["id"], []),
                                    apps_by.get(ext["id"], []), comps_by.get(ext["id"], []))
            rec["status"] = status_of(rec["fields"])
            linked_vod.setdefault(chosen["vodMapId"], []).append(ext["id"])
        records.append(rec)

    # Two spreadsheet maps linked to ONE VOD map cannot both be right.
    for vid, ext_ids in linked_vod.items():
        if len(ext_ids) > 1:
            for r in records:
                if r["externalMapId"] in ext_ids and r["method"] == "auto":
                    r.update(status="needs_review", vodMapId=None, fields={}, method=None,
                             reason=f"{len(ext_ids)} spreadsheet maps would link to {vid}")
    vod_only = sorted(v["vodMapId"] for v in vods
                      if not any(r["vodMapId"] == v["vodMapId"] for r in records))
    summary = {s: sum(1 for r in records if r["status"] == s and not r["ignored"]) for s in STATUSES}
    summary["ignored"] = sum(1 for r in records if r["ignored"])
    summary["linked"] = sum(1 for r in records if r["vodMapId"])
    summary["vodMaps"] = len(vods)
    summary["vodOnly"] = len(vod_only)
    return {"records": records, "vodOnlyMaps": vod_only, "summary": summary,
            "rules": {"autoLinkScore": AUTO_LINK_SCORE, "autoLinkMargin": AUTO_LINK_MARGIN,
                      "reviewScore": REVIEW_SCORE, "briefSeconds": BRIEF_SECONDS}}
