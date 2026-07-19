#!/usr/bin/env python3
"""
capture_hero_crops.py — collect labeled hero-slot crops from a run's frames so
real broadcast templates can be built from actual crops (blueprint Phase 3
prep). This is CAPTURE + REVIEW ONLY: it never writes comps, never touches the
DB comp tables, never promotes, and never edits layout coordinates.

For a run it:
  1. cuts all 10 HUD slot crops from each sampled frame (reusing the exact
     same slot geometry + auto-scaling as build_crop_report.crop_slots), and
     saves them EVEN WHEN detection was quarantined — quarantined frames are
     precisely the crops we need to label,
  2. records per-crop metadata (run, frame, offset, side, slot, crop path,
     current detector guess+score if templates exist, label status, label),
  3. writes a labels.json sidecar (idempotent manual labels — for template
     building only, never comp promotion),
  4. emits an HTML contact sheet (hero_crops.html) linked from the run report:
     raw frame, annotated frame, and all 10 crops with side/slot labels so you
     can SEE whether the layout boxes sit on real hero portraits,
  5. can dry-run (default) or write candidate templates into
     templates/candidates/<hero>/... only — never the real templates/ dir.

Layout artifacts on disk (all under the run's report folder):
  reports/auto/<run>/hero_crops/crops/<frame>_<slot>.png
  reports/auto/<run>/hero_crops/annotated/<frame>_annotated.png
  reports/auto/<run>/hero_crops/crops.json     (crop metadata, regenerated)
  reports/auto/<run>/hero_crops/labels.json    (manual labels, preserved)
  reports/auto/<run>/hero_crops.html           (the review contact sheet)

Usage:
  # capture crops + build the review page for a run
  python pipeline/capture_hero_crops.py --run <run_id>

  # explicit layout / frames (defaults come from data/auto_runs.json + work/)
  python pipeline/capture_hero_crops.py --run <run_id> \
      --layout layouts/owcs_8c105lnzlam.json \
      --frames-dir work/auto/<run_id>/frames_raw

  # label / reject a single crop from the CLI (same effect as the web buttons)
  python pipeline/capture_hero_crops.py --run <run_id> --label 000600_a1=kiriko
  python pipeline/capture_hero_crops.py --run <run_id> --reject 000600_a2

  # see which labeled crops WOULD become candidate templates (writes nothing)
  python pipeline/capture_hero_crops.py --run <run_id> --export-candidates
  # actually write them, into templates/candidates/ only
  python pipeline/capture_hero_crops.py --run <run_id> --export-candidates --write
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import shutil
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402
import detect  # noqa: E402
import hero_overlay_detect as hod  # noqa: E402
import build_layout_debug as bld  # noqa: E402
import build_crop_report as bcr  # noqa: E402

MAX_FRAMES = 12          # contact sheet stays light; earliest N frames
LOW_FLOOR = bcr.LOW_FLOOR
TEMPLATES_ROOT = os.path.join(db.REPO_ROOT, "templates")
AUTO_RUNS_PATH = os.path.join(db.REPO_ROOT, "data", "auto_runs.json")


def log(msg: str) -> None:
    print(f"[hero-crops] {msg}", flush=True)


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("'", "&#39;"))


# ---------------------------------------------------------- run path helpers
def run_report_dir(run: str) -> str:
    return os.path.join(db.REPO_ROOT, "reports", "auto", run)


def run_frames_dir(run: str) -> str:
    return os.path.join(db.REPO_ROOT, "work", "auto", run, "frames_raw")


def hero_dir(report_dir: str) -> str:
    return os.path.join(report_dir, "hero_crops")


def crops_json_path(report_dir: str) -> str:
    return os.path.join(hero_dir(report_dir), "crops.json")


def labels_json_path(report_dir: str) -> str:
    return os.path.join(hero_dir(report_dir), "labels.json")


def contact_sheet_path(report_dir: str) -> str:
    return os.path.join(report_dir, "hero_crops.html")


def find_run_record(run: str, auto_runs_path: str | None = None) -> dict | None:
    path = auto_runs_path or AUTO_RUNS_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            runs = json.load(f).get("runs", [])
    except (OSError, ValueError):
        return None
    for r in runs:
        if r.get("run") == run:
            return r
    return None


# ------------------------------------------------------------------- heroes
def load_heroes(db_path: str | None = None):
    """(heroes, message). heroes = [{id,name,role}] sorted; message names the
    fix when the DB is not initialized. Never auto-creates the DB."""
    path = db_path or db.DB_PATH
    fix = ("hero list unavailable — initialize the DB with "
           "`python pipeline/init_db.py --with-sample`")
    if not os.path.exists(path):
        return [], fix
    try:
        con = db.connect(path)
        rows = con.execute(
            "SELECT id, name, role FROM heroes ORDER BY role, name").fetchall()
        con.close()
    except Exception as e:                      # table missing / unreadable
        return [], f"{fix} ({type(e).__name__}: {e})"
    if not rows:
        return [], fix
    return [{"id": r["id"], "name": r["name"], "role": r["role"]}
            for r in rows], None


# ------------------------------------------------------------------- labels
def load_labels(report_dir: str) -> dict:
    try:
        with open(labels_json_path(report_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_labels(report_dir: str, labels: dict) -> None:
    os.makedirs(hero_dir(report_dir), exist_ok=True)
    with open(labels_json_path(report_dir), "w", encoding="utf-8") as f:
        json.dump(labels, f, indent=2, sort_keys=True)


def _apply_labels(crops: list[dict], labels: dict) -> None:
    """Overlay manual label state onto crop metadata (in place)."""
    for c in crops:
        lab = labels.get(c["id"])
        if lab:
            c["label_status"] = lab.get("status", "labeled")
            c["label"] = lab.get("hero")
        else:
            c["label_status"] = "unlabeled"
            c["label"] = None


# ----------------------------------------------------------------- metadata
def load_meta(report_dir: str) -> dict | None:
    """crops.json with the latest labels re-applied, or None if not captured."""
    try:
        with open(crops_json_path(report_dir), "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    _apply_labels(meta.get("crops", []), load_labels(report_dir))
    return meta


def _save_meta(report_dir: str, meta: dict) -> None:
    os.makedirs(hero_dir(report_dir), exist_ok=True)
    with open(crops_json_path(report_dir), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def set_label(report_dir: str, crop_id: str, hero: str):
    """Manual label (template-building only — never promotes a comp).
    Returns (crop_dict, None) or (None, error). Idempotent."""
    meta = load_meta(report_dir)
    if meta is None:
        return None, "run has no captured crops yet"
    entry = next((c for c in meta["crops"] if c["id"] == crop_id), None)
    if entry is None:
        return None, f"unknown crop id: {crop_id}"
    if not hero or not str(hero).strip():
        return None, "hero label required"
    hero = str(hero).strip()
    labels = load_labels(report_dir)
    labels[crop_id] = {"status": "labeled", "hero": hero}
    save_labels(report_dir, labels)
    _apply_labels(meta["crops"], labels)
    _save_meta(report_dir, meta)
    return next(c for c in meta["crops"] if c["id"] == crop_id), None


def reject_crop(report_dir: str, crop_id: str):
    """Mark a crop unusable (bad box / not a portrait). Idempotent."""
    meta = load_meta(report_dir)
    if meta is None:
        return None, "run has no captured crops yet"
    entry = next((c for c in meta["crops"] if c["id"] == crop_id), None)
    if entry is None:
        return None, f"unknown crop id: {crop_id}"
    labels = load_labels(report_dir)
    labels[crop_id] = {"status": "rejected", "hero": None}
    save_labels(report_dir, labels)
    _apply_labels(meta["crops"], labels)
    _save_meta(report_dir, meta)
    return next(c for c in meta["crops"] if c["id"] == crop_id), None


# ------------------------------------------------------------------ capture
def _try_load_lib(layout: dict, templates_dir: str | None):
    try:
        return hod.load_lib(layout, templates_dir), None
    except FileNotFoundError as e:
        return None, str(e)


def capture_run(run: str, layout: dict, frames_dir: str, report_dir: str,
                templates_dir: str | None = None,
                max_frames: int = MAX_FRAMES) -> dict:
    """Cut + save all 10 slot crops per frame and (re)build the review page.

    Preserves any existing labels.json. Returns a summary dict.
    """
    if not os.path.isdir(frames_dir):
        raise FileNotFoundError(f"no frames dir: {frames_dir}")
    frames = sorted(f for f in os.listdir(frames_dir)
                    if f.lower().endswith((".png", ".jpg", ".jpeg")))
    hdir = hero_dir(report_dir)
    crops_dir = os.path.join(hdir, "crops")
    ann_dir = os.path.join(hdir, "annotated")
    os.makedirs(crops_dir, exist_ok=True)
    os.makedirs(ann_dir, exist_ok=True)

    lib, lib_reason = _try_load_lib(layout, templates_dir)
    threshold = layout.get("match_threshold", 0.6)
    crops_meta: list[dict] = []
    frames_meta: list[dict] = []
    n_crops = n_bad = 0

    for fn in frames[:max_frames]:
        frame = cv2.imread(os.path.join(frames_dir, fn))
        if frame is None:
            continue
        fh, fw = frame.shape[:2]
        _, sinfo = capture.scale_layout_to_frame(layout, fw, fh)
        base = os.path.splitext(fn)[0]
        offset = int(base) if base.isdigit() else None

        ann_fn = f"{base}_annotated.png"
        cv2.imwrite(os.path.join(ann_dir, ann_fn), bld.draw_layout(frame, layout))
        raw_rel = os.path.relpath(os.path.join(frames_dir, fn), report_dir)

        slot_ids: list[str] = []
        for s in bcr.crop_slots(frame, layout):
            slot_id = f"{s['side']}{s['i']}"
            cid = f"{base}_{slot_id}"
            slot_ids.append(cid)
            entry = {
                "id": cid, "run": run, "frame": fn, "offset": offset,
                "side": s["side"], "slot": slot_id, "crop": None,
                "guess": None, "score": None, "bad": False, "note": "",
                "label_status": "unlabeled", "label": None,
            }
            if s["crop"] is None:                       # box outside frame etc.
                entry["bad"] = True
                entry["note"] = s["note"]
                n_bad += 1
            else:
                crop_fn = f"{base}_{slot_id}.png"
                cv2.imwrite(os.path.join(crops_dir, crop_fn), s["crop"])
                entry["crop"] = os.path.join("hero_crops", "crops",
                                             crop_fn).replace("\\", "/")
                n_crops += 1
                if lib:
                    gray = cv2.cvtColor(s["crop"], cv2.COLOR_BGR2GRAY)
                    hero, score = detect.match_slot(gray, lib)
                    entry["guess"] = hero or None
                    entry["score"] = round(float(score), 3)
            crops_meta.append(entry)

        frames_meta.append({
            "frame": fn, "offset": offset,
            "raw": raw_rel.replace("\\", "/"),
            "annotated": os.path.join("hero_crops", "annotated",
                                      ann_fn).replace("\\", "/"),
            "size": [fw, fh], "scaleNote": sinfo["note"],
            "scaleOk": sinfo["ok"], "cropIds": slot_ids,
        })

    _apply_labels(crops_meta, load_labels(report_dir))   # keep prior labels
    heroes, hero_msg = load_heroes()
    meta = {
        "run": run,
        "layout": layout.get("_path") or layout.get("_dir") or "",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "templates": bool(lib),
        "templatesNote": lib_reason or "",
        "threshold": threshold,
        "frameCount": len(frames_meta),
        "cropCount": n_crops,
        "badCount": n_bad,
        "heroesAvailable": bool(heroes),
        "frames": frames_meta,
        "crops": crops_meta,
    }
    _save_meta(report_dir, meta)
    if not os.path.exists(labels_json_path(report_dir)):
        save_labels(report_dir, {})

    html = build_contact_sheet(meta, heroes, hero_msg)
    with open(contact_sheet_path(report_dir), "w", encoding="utf-8") as f:
        f.write(html)

    return {
        "run": run, "frames": len(frames_meta), "crops": n_crops,
        "bad": n_bad, "templates": bool(lib), "templatesNote": lib_reason or "",
        "html": contact_sheet_path(report_dir),
        "cropsJson": crops_json_path(report_dir),
        "labels": labels_json_path(report_dir),
        "heroesMessage": hero_msg,
    }


# ------------------------------------------------------- candidate templates
def export_candidates(run: str, report_dir: str,
                      templates_root: str | None = None,
                      write: bool = False) -> dict:
    """Which LABELED crops would become candidate templates.

    Dry-run by default. When write=True, copies ONLY into
    templates/candidates/<hero>/ — never the real templates/ dir, verified by
    a path-containment assertion. Existing real templates are never touched.
    """
    troot = templates_root or TEMPLATES_ROOT
    cand_root = os.path.join(troot, "candidates")
    meta = load_meta(report_dir)
    if meta is None:
        return {"planned": [], "wrote": False, "count": 0,
                "candidatesDir": cand_root,
                "error": "run has no captured crops yet"}

    planned: list[dict] = []
    for c in meta["crops"]:
        if c["label_status"] != "labeled" or not c["label"] or c["bad"]:
            continue
        if not c.get("crop"):
            continue
        base = os.path.splitext(c["frame"])[0]
        hero = c["label"]
        dest = os.path.join(cand_root, hero, f"{run}_{c['slot']}_{base}.png")
        planned.append({
            "crop_id": c["id"], "hero": hero,
            "src": os.path.join(report_dir, c["crop"]),
            "dest": dest,
        })

    wrote = False
    cand_norm = os.path.normpath(cand_root)
    if write and planned:
        for p in planned:
            dest_norm = os.path.normpath(p["dest"])
            # Hard safety: candidates ONLY. Never the real templates/ dir.
            if os.path.commonpath([dest_norm, cand_norm]) != cand_norm:
                raise RuntimeError(
                    f"refusing to write outside candidates/: {p['dest']}")
            os.makedirs(os.path.dirname(dest_norm), exist_ok=True)
            shutil.copy(p["src"], dest_norm)
        wrote = True

    return {"planned": planned, "wrote": wrote, "count": len(planned),
            "candidatesDir": cand_root, "error": None}


# ---------------------------------------------------------- contact sheet UI
_CSS = """
:root{--bg:#060b15;--surface:#111c31;--line:#1f2e4d;--text:#e9eef7;
--muted:#8ea0bd;--amber:#ffa92b}
*{box-sizing:border-box}
body{font-family:Inter,"Segoe UI",system-ui,sans-serif;max-width:1200px;
margin:0 auto;padding:26px 18px 60px;color:var(--text);background:var(--bg);
line-height:1.55}
h1{font-family:"Chakra Petch","Segoe UI",sans-serif;font-size:1.35rem}
h2{font-family:"Chakra Petch",sans-serif;font-size:.95rem;margin-top:26px;
text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
a{color:var(--amber);text-decoration:none}a:hover{text-decoration:underline}
code{background:rgba(255,255,255,.07);padding:1px 6px;border-radius:4px;
font-family:ui-monospace,Consolas,monospace;font-size:.85em}
.note{border:1px solid rgba(255,169,43,.5);border-left:4px solid var(--amber);
background:rgba(255,169,43,.10);padding:10px 14px;border-radius:10px;margin:14px 0}
.warn{border:1px solid rgba(232,161,60,.5);border-left:4px solid #e8a13c;
background:rgba(232,161,60,.12);padding:10px 14px;border-radius:10px;margin:12px 0}
.frames{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0}
.frames figure{margin:0;flex:1 1 320px;max-width:520px}
.frames img{width:100%;border:1px solid var(--line);border-radius:8px}
.frames figcaption{color:var(--muted);font-size:.75rem;text-align:center}
.strip{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0}
.cell{border:1px solid var(--line);border-radius:10px;padding:8px;
background:var(--surface);text-align:center;font-size:.72rem;width:120px;
color:var(--muted)}
.cell img{width:104px;border:1px solid var(--line);display:block;margin:3px auto;
border-radius:4px;background:#050a13}
.pill{display:inline-block;color:#fff;border-radius:999px;padding:0 8px;
font-family:"Chakra Petch",sans-serif;font-weight:700;font-size:.6rem;
letter-spacing:.05em;margin:2px 0}
.cell select{width:104px;font-size:.72rem;background:#0a1322;color:var(--text);
border:1px solid var(--line);border-radius:5px;padding:2px;margin-top:4px}
.cell button{font-size:.68rem;border:1px solid var(--line);border-radius:5px;
background:#0a1322;color:var(--text);cursor:pointer;padding:2px 6px;margin:3px 2px 0}
.cell button:disabled{opacity:.4;cursor:not-allowed}
.st-labeled{color:#2ebd6b}.st-rejected{color:#ff5c64}.st-unlabeled{color:var(--muted)}
"""

_LABEL_COLORS = {"OK": "#2ebd6b", "LOW": "#e8a13c", "NO-MATCH": "#ff5c64"}


def _score_label(score: float, threshold: float) -> str:
    if score >= threshold:
        return "OK"
    if score >= LOW_FLOOR:
        return "LOW"
    return "NO-MATCH"


def _cell_html(c: dict, threshold: float) -> str:
    slot = _esc(c["slot"])
    if c["bad"]:
        return (f"<div class='cell' data-crop='{slot}'>{slot}<br>"
                "<span class='pill' style='background:#c62828'>BAD BOX</span>"
                f"<br><span class='muted'>{_esc(c['note'])}</span></div>")
    img = f"<img src='{_esc(c['crop'])}' alt='{slot}'>"
    guess = ""
    if c["guess"] is not None and c["score"] is not None:
        lab = _score_label(c["score"], threshold)
        guess = (f"<span class='pill' style='background:{_LABEL_COLORS[lab]}'>"
                 f"{lab}</span><br><span class='muted'>{_esc(c['guess'])} "
                 f"{c['score']:.2f}</span>")
    else:
        guess = "<span class='muted'>no templates</span>"
    status = c.get("label_status", "unlabeled")
    label = c.get("label")
    status_line = (f"<div class='status st-{_esc(status)}' "
                   f"data-status>{_esc(status)}"
                   + (f": {_esc(label)}" if label else "") + "</div>")
    controls = (
        "<select data-hero></select>"
        f"<div><button data-act='label' data-id='{_esc(c['id'])}'>label</button>"
        f"<button data-act='reject' data-id='{_esc(c['id'])}'>reject</button></div>")
    return (f"<div class='cell' data-crop-id='{_esc(c['id'])}'>{slot}<br>{img}"
            f"{guess}{status_line}{controls}</div>")


def build_contact_sheet(meta: dict, heroes: list[dict],
                        hero_msg: str | None) -> str:
    run = meta["run"]
    threshold = meta.get("threshold", 0.6)
    by_id = {c["id"]: c for c in meta["crops"]}
    sections = []
    for fm in meta["frames"]:
        cells = "".join(_cell_html(by_id[cid], threshold)
                        for cid in fm["cropIds"] if cid in by_id)
        got = sum(1 for cid in fm["cropIds"]
                  if cid in by_id and not by_id[cid]["bad"])
        scale = ("" if fm.get("scaleOk", True)
                 else f" — <span style='color:#ff5c64'>"
                      f"{_esc(fm.get('scaleNote'))}</span>")
        sections.append(
            f"<h2>{_esc(fm['frame'])} — {got}/10 slots cropped{scale}</h2>"
            "<div class='frames'>"
            f"<figure><a href='{_esc(fm['raw'])}'><img src='{_esc(fm['raw'])}'>"
            "</a><figcaption>raw frame</figcaption></figure>"
            f"<figure><a href='{_esc(fm['annotated'])}'>"
            f"<img src='{_esc(fm['annotated'])}'></a>"
            "<figcaption>annotated (layout boxes)</figcaption></figure></div>"
            f"<div class='strip'>{cells}</div>")

    tpl_note = ("" if meta.get("templates") else
                f"<div class='warn'>No hero templates yet "
                f"({_esc(meta.get('templatesNote') or '')}). Crops still "
                "captured — label the clean ones below, then export candidate "
                "templates.</div>")
    hero_note = ("" if not hero_msg else
                 f"<div class='warn'>{_esc(hero_msg)}</div>")
    body = "".join(sections) or "<p class='muted'>No readable frames.</p>"
    data = json.dumps({"run": run, "heroes": heroes,
                       "threshold": threshold}, separators=(",", ":"))
    script = _SCRIPT.replace("__DATA__", data)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{_esc(run)} — hero crop review</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<h1>Hero crop review — {_esc(run)}</h1>"
        "<div class='note'><strong>Hero crop review/capture only — does not "
        "write comps.</strong> Labels here build real broadcast templates; they "
        "never promote a composition. Verify each box sits on exactly one hero "
        "portrait.</div>"
        f"{tpl_note}{hero_note}"
        "<p><a href='index.html'>run report</a> · "
        "<a href='crops.html'>crop+score report</a> · "
        "<a href='layout.html'>layout debug</a> · "
        "<a href='../../../runs.html'>all runs</a> · "
        "<a href='../../../admin.html'>manual corrections</a></p>"
        f"<div id='status-bar' class='muted'></div>"
        f"{body}"
        f"<script>{script}</script>"
        "</body></html>")


# Vanilla JS: labeling POSTs to the serve.py API when it is running; otherwise
# the buttons disable and the page stays a read-only visual review + the CLI
# command is shown. No framework, no build step.
_SCRIPT = r"""
const D = __DATA__;
const bar = document.getElementById('status-bar');
function fillHeroes(sel, cur){
  sel.innerHTML = "<option value=''>— hero —</option>" +
    D.heroes.map(h => "<option value='"+h.id+"'"+
      (h.id===cur?" selected":"")+">"+h.name+"</option>").join("");
}
function setStatus(cell, st, hero){
  const s = cell.querySelector('[data-status]');
  s.className = 'status st-'+st;
  s.textContent = st + (hero ? (': '+hero) : '');
}
async function ping(){
  try{ const r = await fetch('/api/ping'); return r.ok; }catch(e){ return false; }
}
async function act(kind, id, hero, cell){
  const base = '/api/runs/'+encodeURIComponent(D.run)+'/hero-crops/'+
               encodeURIComponent(id)+'/'+kind;
  const body = kind==='label' ? JSON.stringify({hero}) : '{}';
  const r = await fetch(base, {method:'POST',
    headers:{'Content-Type':'application/json'}, body});
  const j = await r.json();
  if(!r.ok){ bar.textContent = 'error: '+(j.error||r.status); return; }
  setStatus(cell, j.label_status, j.label);
  bar.textContent = id+' → '+j.label_status+(j.label?(' ('+j.label+')'):'');
}
document.querySelectorAll('.cell[data-crop-id]').forEach(cell=>{
  const sel = cell.querySelector('[data-hero]');
  if(sel) fillHeroes(sel, null);
});
(async ()=>{
  const live = await ping();
  const cmd = 'python pipeline/capture_hero_crops.py --run '+D.run+
              ' --label <crop_id>=<hero>';
  if(!live){
    bar.innerHTML = "API not running — this page is read-only. "+
      "Start it with <code>python pipeline/serve.py</code> to label here, "+
      "or from a terminal: <code>"+cmd+"</code>";
    document.querySelectorAll('.cell button').forEach(b=>b.disabled=true);
    return;
  }
  bar.textContent = 'API connected — label/reject saved to labels.json '+
                    '(template-building only, never promotes comps).';
  document.querySelectorAll('.cell button').forEach(b=>{
    b.addEventListener('click', ()=>{
      const cell = b.closest('.cell');
      const id = b.getAttribute('data-id');
      if(b.getAttribute('data-act')==='label'){
        const hero = cell.querySelector('[data-hero]').value;
        if(!hero){ bar.textContent='pick a hero first'; return; }
        act('label', id, hero, cell);
      } else { act('reject', id, null, cell); }
    });
  });
})();
"""


# ------------------------------------------------------------------- CLI
def _resolve_layout_path(run: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    rec = find_run_record(run)
    if rec and rec.get("layout"):
        return rec["layout"]
    raise SystemExit(
        f"no --layout given and no layout recorded for run '{run}' in "
        f"{AUTO_RUNS_PATH}; pass --layout layouts/<name>.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hero crop capture + review")
    ap.add_argument("--run", required=True, help="auto run id")
    ap.add_argument("--layout", default=None,
                    help="layout JSON (default: the run's recorded layout)")
    ap.add_argument("--frames-dir", default=None,
                    help="frames dir (default: work/auto/<run>/frames_raw)")
    ap.add_argument("--report-dir", default=None,
                    help="report dir (default: reports/auto/<run>)")
    ap.add_argument("--templates-dir", default=None)
    ap.add_argument("--max-frames", type=int, default=MAX_FRAMES)
    ap.add_argument("--label", metavar="CROP_ID=HERO", default=None,
                    help="set a manual label, then exit")
    ap.add_argument("--reject", metavar="CROP_ID", default=None,
                    help="reject a crop, then exit")
    ap.add_argument("--export-candidates", action="store_true",
                    help="show labeled crops that would become candidates")
    ap.add_argument("--write", action="store_true",
                    help="with --export-candidates: actually write into "
                         "templates/candidates/ (never real templates/)")
    args = ap.parse_args(argv)

    report_dir = args.report_dir or run_report_dir(args.run)

    if args.label:
        if "=" not in args.label:
            raise SystemExit("--label must be CROP_ID=HERO")
        cid, hero = args.label.split("=", 1)
        entry, err = set_label(report_dir, cid.strip(), hero.strip())
        if err:
            raise SystemExit(f"label failed: {err}")
        log(f"labeled {cid.strip()} = {hero.strip()}")
        return 0

    if args.reject:
        entry, err = reject_crop(report_dir, args.reject.strip())
        if err:
            raise SystemExit(f"reject failed: {err}")
        log(f"rejected {args.reject.strip()}")
        return 0

    if args.export_candidates:
        res = export_candidates(args.run, report_dir, write=args.write)
        if res.get("error"):
            raise SystemExit(res["error"])
        mode = "WROTE" if res["wrote"] else "DRY-RUN (nothing written)"
        log(f"candidate templates — {mode}: {res['count']} planned")
        for p in res["planned"]:
            log(f"  {p['hero']:<12} {p['crop_id']:<14} -> "
                f"{os.path.relpath(p['dest'], db.REPO_ROOT)}")
        if not res["wrote"]:
            log("re-run with --write to copy these into templates/candidates/")
        return 0

    layout_path = _resolve_layout_path(args.run, args.layout)
    layout = capture.load_layout(layout_path)
    layout["_path"] = layout_path
    frames_dir = args.frames_dir or run_frames_dir(args.run)
    res = capture_run(args.run, layout, frames_dir, report_dir,
                      templates_dir=args.templates_dir,
                      max_frames=args.max_frames)
    log(f"{res['crops']} crop(s) from {res['frames']} frame(s) "
        f"({res['bad']} bad box) -> {res['html']}")
    if not res["templates"]:
        log(f"no templates yet ({res['templatesNote']}) — crops only")
    if res.get("heroesMessage"):
        log(res["heroesMessage"])
    log(f"review: open reports/auto/{args.run}/hero_crops.html "
        "(run `python pipeline/serve.py` to label in-browser)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
