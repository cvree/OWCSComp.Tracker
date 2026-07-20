#!/usr/bin/env python3
"""
build_ingest_report.py — human-readable evidence pages for a map ingestion.

Reads the JSON artifacts written by ingest_map.py under
reports/ingest/<ingest-id>/ and produces, in the same folder:

  report.html   the full-map report: coverage, rounds, per-slot composition
                timelines, per-round initial comps, confidence
                distribution, lowest-confidence accepted observation,
                calibration diagnostics, DB write summary
  review.html   the change-point review page: EVERY confirmed swap,
                rejected suspected swap and setup change, with evidence
                crops, timestamps and accept/reject reasoning

Both are static, self-contained (inline CSS), and reference the evidence
crops relatively so the folder can be served or zipped as a unit.

Usage:
  python pipeline/build_ingest_report.py --ingest-id qad-twis-nepal
"""
from __future__ import annotations
import argparse
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

CSS = """
body{background:#0d1420;color:#dce6f2;font:14px/1.5 'Segoe UI',system-ui,
sans-serif;margin:0;padding:24px;max-width:1180px;margin:auto}
h1{font-size:22px;color:#ffb52e}h2{font-size:17px;color:#8fb7e8;
border-bottom:1px solid #223349;padding-bottom:4px;margin-top:28px}
table{border-collapse:collapse;width:100%;margin:10px 0}
td,th{border:1px solid #223349;padding:5px 8px;text-align:left;
font-size:13px}th{background:#16233a;color:#8fb7e8}
.pill{display:inline-block;padding:1px 8px;border-radius:9px;
font-size:12px;font-weight:600}
.ok{background:#123c26;color:#5ee49a}.warn{background:#3c3212;
color:#e4c95e}.bad{background:#3c1212;color:#e45e5e}
.muted{color:#6e83a0}img.crop{width:64px;height:64px;
image-rendering:pixelated;border:1px solid #223349;border-radius:4px}
.evrow img{margin-right:6px}.mono{font-family:Consolas,monospace;
font-size:12px}
.timeline{display:flex;height:26px;border-radius:4px;overflow:hidden;
margin:2px 0 10px}
.timeline div{height:100%;display:flex;align-items:center;
justify-content:center;font-size:11px;font-weight:600;color:#08111e;
white-space:nowrap;overflow:hidden}
a{color:#6fb1ff}
"""

HERO_COLORS = ["#ffb52e", "#5ee49a", "#6fb1ff", "#e45e9a", "#e4c95e",
               "#9a7fe8", "#5ed8e4", "#e4835e", "#a8e45e", "#e45e5e"]


def esc(x) -> str:
    return html.escape(str(x))


def load_artifacts(root: str) -> dict:
    out = {}
    for name in ("stints", "rounds", "stats"):
        with open(os.path.join(root, f"{name}.json"), encoding="utf-8") as f:
            out[name] = json.load(f)
    obs = []
    with open(os.path.join(root, "observations.jsonl"),
              encoding="utf-8") as f:
        for line in f:
            obs.append(json.loads(line))
    out["observations"] = obs
    return out


def hero_color(hero: str, palette: dict) -> str:
    if hero not in palette:
        palette[hero] = HERO_COLORS[len(palette) % len(HERO_COLORS)]
    return palette[hero]


def timeline_html(stints: list, t0: float, t1: float, palette: dict) -> str:
    if not stints:
        return "<div class='muted'>no established hero</div>"
    parts = ["<div class='timeline'>"]
    span = t1 - t0
    cursor = t0
    for s in stints:
        gap = s["start"] - cursor
        if gap > 1:
            parts.append(
                f"<div style='width:{100 * gap / span:.2f}%;"
                f"background:#16233a'></div>")
        w = 100 * (s["end"] - s["start"]) / span
        c = hero_color(s["hero"], palette)
        parts.append(
            f"<div style='width:{w:.2f}%;background:{c}' "
            f"title='{esc(s['hero'])} {s['start']:.0f}-{s['end']:.0f}s'>"
            f"{esc(s['hero'])}</div>")
        cursor = s["end"]
    parts.append("</div>")
    return "".join(parts)


def fmt_t(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def crop_img(name: str | None) -> str:
    if not name:
        return "<span class='muted'>—</span>"
    return f"<img class='crop' src='evidence/{esc(name)}' title='{esc(name)}'>"


def build_report(root: str, art: dict, layout: dict | None,
                 db_info: dict | None, pairing: dict) -> str:
    stats = art["stats"]
    rounds = art["rounds"]["rounds"]
    sides = art["rounds"].get("sides", [])
    obs = art["observations"]
    palette: dict = {}
    t0, t1 = stats["window"]

    accepted = []
    for o in obs:
        if o["state"] != "gameplay":
            continue
        for k, r in (o.get("slots") or {}).items():
            if r.get("reject") is None and r.get("hero") not in (
                    "", "UNKNOWN"):
                accepted.append((r["score"], o["t"], k, r["hero"]))
    accepted.sort()
    n_acc = len(accepted)
    buckets = {"<0.5": 0, "0.5-0.7": 0, "0.7-0.9": 0, ">=0.9": 0}
    for (s, _t, _k, _h) in accepted:
        if s < 0.5:
            buckets["<0.5"] += 1
        elif s < 0.7:
            buckets["0.5-0.7"] += 1
        elif s < 0.9:
            buckets["0.7-0.9"] += 1
        else:
            buckets[">=0.9"] += 1

    H = [f"<meta charset='utf-8'><style>{CSS}</style>"
         f"<h1>Full-map ingestion report — "
         f"{esc(pairing.get('title', root))}</h1>"]
    H.append(f"<p class='muted'>window {fmt_t(t0)}–{fmt_t(t1)} (stream "
             f"offsets {t0:.0f}–{t1:.0f}s) · detector "
             f"{esc(stats['detector_version'])} · generated from "
             f"observations.jsonl / stints.json — see "
             f"<a href='review.html'>change-point review</a> · "
             f"<a href='crops.html'>hero crop report</a></p>")

    H.append("<h2>Coverage</h2><table>")
    rows = [
        ("frames sampled", stats["frames_sampled"]),
        ("gameplay frames (hero-readable)", stats["gameplay_frames"]),
        ("skipped frames (with reasons)", stats["skipped_frames"]),
        ("dense confirmation windows", stats["dense_windows"]),
        ("accepted slot observations", n_acc),
        ("confirmed swaps", stats["confirmed_swaps"]),
        ("rejected suspected swaps", stats["rejected_swaps"]),
        ("setup-phase comp changes", stats["setup_changes"]),
    ]
    for k, v in rows:
        H.append(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>")
    H.append("</table>")

    H.append("<h2>Rounds</h2><table><tr><th>round</th><th>start</th>"
             "<th>end</th><th>confidence</th><th>side check</th></tr>")
    dec = {d["index"]: d for d in sides}
    for r in rounds:
        d = dec.get(r["index"], {})
        side_txt = ("<span class='pill bad'>SWAPPED</span> "
                    if d.get("swapped")
                    else "<span class='pill ok'>unchanged</span> ")
        side_txt += esc(d.get("note", ""))
        H.append(f"<tr><td>{r['index']}</td><td>{fmt_t(r['start'])}</td>"
                 f"<td>{fmt_t(r['end'])}</td><td>{r['confidence']}</td>"
                 f"<td>{side_txt}</td></tr>")
    H.append("</table>")

    H.append("<h2>Composition timelines (per slot)</h2>")
    slot_keys = [f"{s}{i}" for s in ("a", "b") for i in range(1, 6)]
    for key in slot_keys:
        data = art["stints"].get(key) or {}
        stints = data.get("stints") or []
        H.append(f"<div class='mono'>{key} — "
                 f"{esc(pairing.get('team_' + key[0], key[0]))} · "
                 f"{data.get('n_reads', 0)} accepted reads</div>")
        H.append(timeline_html(stints, t0, t1, palette))

    H.append("<h2>Initial composition per round</h2><table>"
             "<tr><th>round</th><th>side a</th><th>side b</th></tr>")
    for r in rounds:
        comps = {"a": [], "b": []}
        for key in slot_keys:
            for s in (art["stints"].get(key) or {}).get("stints", []):
                if s["start"] <= r["start"] + 15 and \
                        s["end"] >= r["start"]:
                    comps[key[0]].append(s["hero"])
                    break
            else:
                comps[key[0]].append("?")
        H.append(f"<tr><td>{r['index']}</td>"
                 f"<td>{esc(' '.join(comps['a']))}</td>"
                 f"<td>{esc(' '.join(comps['b']))}</td></tr>")
    H.append("</table>")

    H.append("<h2>Confirmed swaps</h2>")
    any_swap = False
    for key in slot_keys:
        for e in (art["stints"].get(key) or {}).get("events", []):
            if e["kind"] != "swap":
                continue
            any_swap = True
            H.append(
                "<div class='evrow'>"
                f"<b>{key}</b>: {esc(e['from'])} → {esc(e['to'])} at "
                f"{fmt_t(e['t'])} ({e['t']:.0f}s) · confidence "
                f"{e['confidence']} · margin {e['margin']} · "
                f"{e['n_obs']} obs<br>"
                f"{crop_img(e.get('evidence_before'))}"
                f"{crop_img(e.get('evidence_after'))} "
                f"<span class='muted'>{esc(e['reason'])}</span></div>")
    if not any_swap:
        H.append("<p class='muted'>none</p>")

    H.append("<h2>Confidence distribution (accepted reads)</h2><table>")
    for k, v in buckets.items():
        H.append(f"<tr><th>{esc(k)}</th><td>{v} "
                 f"({100 * v / max(1, n_acc):.1f}%)</td></tr>")
    H.append("</table>")
    if accepted:
        s, t, k, h = accepted[0]
        H.append(f"<p>lowest-confidence accepted observation: "
                 f"<b>{esc(h)}</b> in {k} at {fmt_t(t)} "
                 f"(score {s:.2f})</p>")

    if layout and layout.get("calibration"):
        c = layout["calibration"]
        H.append("<h2>Calibration diagnostics</h2><table>")
        for k in ("version", "source_id", "calibrated_at_resolution",
                  "frames_used", "confidence", "portrait_direction"):
            H.append(f"<tr><th>{esc(k)}</th>"
                     f"<td class='mono'>{esc(c.get(k))}</td></tr>")
        warn = c.get("warnings") or []
        H.append(f"<tr><th>warnings</th><td>{esc('; '.join(warn) or '—')}"
                 "</td></tr></table>")

    H.append("<h2>Database</h2>")
    if db_info:
        H.append("<table>")
        for k, v in db_info.items():
            H.append(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>")
        H.append("</table>")
    else:
        H.append("<p class='muted'>dry run — nothing written</p>")
    return "".join(H)


def build_review(root: str, art: dict, pairing: dict) -> str:
    slot_keys = [f"{s}{i}" for s in ("a", "b") for i in range(1, 6)]
    H = [f"<meta charset='utf-8'><style>{CSS}</style>"
         f"<h1>Change-point review — "
         f"{esc(pairing.get('title', root))}</h1>"
         "<p class='muted'>Every suspected hero change, confirmed or "
         "rejected, with evidence. Back to "
         "<a href='report.html'>full report</a> · "
         "<a href='crops.html'>hero crop report</a>.</p>"]
    kinds = [("swap", "Confirmed swaps", "ok"),
             ("setup-change", "Setup-phase comp changes", "warn"),
             ("rejected-swap", "Rejected suspected swaps", "bad"),
             ("unestablished", "Unestablished slots", "bad")]
    for kind, title, pill in kinds:
        H.append(f"<h2>{esc(title)}</h2>")
        n = 0
        for key in slot_keys:
            for e in (art["stints"].get(key) or {}).get("events", []):
                if e["kind"] != kind:
                    continue
                n += 1
                frm = e.get("from") or e.get("slot_from") or "?"
                to = e.get("to") or e.get("candidate") or "?"
                evs = ([e.get("evidence_before"), e.get("evidence_after")]
                       if kind in ("swap", "setup-change")
                       else (e.get("evidence") or []))
                H.append(
                    "<div class='evrow' style='margin:10px 0;padding:8px;"
                    "border:1px solid #223349;border-radius:6px'>"
                    f"<span class='pill {pill}'>{esc(kind)}</span> "
                    f"<b>{key}</b> {esc(frm)} → {esc(to)} at "
                    f"{fmt_t(e['t'])} ({e['t']:.0f}s)"
                    + (f" · conf {e['confidence']}"
                       if e.get("confidence") is not None else "")
                    + f" · {e.get('n_obs', 0)} obs<br>"
                    + "".join(crop_img(x) for x in evs if x)
                    + f"<br><span class='muted'>{esc(e['reason'])}</span>"
                    "</div>")
        if not n:
            H.append("<p class='muted'>none</p>")
    return "".join(H)


def build(ingest_id: str, layout_path: str | None = None,
          db_info: dict | None = None, pairing: dict | None = None) -> str:
    root = os.path.join(db.REPO_ROOT, "reports", "ingest", ingest_id)
    art = load_artifacts(root)
    layout = None
    if layout_path and os.path.exists(layout_path):
        with open(layout_path, encoding="utf-8") as f:
            layout = json.load(f)
    pairing = pairing or {}
    with open(os.path.join(root, "report.html"), "w",
              encoding="utf-8") as f:
        f.write(build_report(root, art, layout, db_info, pairing))
    with open(os.path.join(root, "review.html"), "w",
              encoding="utf-8") as f:
        f.write(build_review(root, art, pairing))
    return root


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest-id", required=True)
    ap.add_argument("--layout")
    ap.add_argument("--title")
    args = ap.parse_args(argv)
    root = build(args.ingest_id, args.layout,
                 pairing={"title": args.title or args.ingest_id})
    print(f"[report] wrote {root}/report.html and review.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
