# OWCS Comp Tracker — Windows Workflow (calibration phase)

This is the short, safe path for running the vision loop on Windows **before**
any real hero-detection or comp-writing work. When you are unsure what to do
next, run the doctor — it tells you.

> Phase reminder: we are still in **calibration / ingestion**. No comps are
> written to the DB, no full-VOD passes, no template approval yet.

---

## 0. Install (once)

```
py -m pip install --upgrade pip
py -m pip install yt-dlp
```

`ffmpeg` must be on PATH. Quick check:

```
ffmpeg -version
yt-dlp --version
```

If either is missing, install ffmpeg (e.g. `winget install Gyan.FFmpeg`) and
re-open the terminal so PATH refreshes.

---

## 1. The one command to run when confused

```
py pipeline\owcs_doctor.py --run <run_id> --layout <layout.json>
```

Example:

```
py pipeline\owcs_doctor.py --run owcs-8c105lnzlam_000600_000630 --layout layouts\owcs_8c105lnzlam.json
```

It checks the DB, folders, layout, run artifacts, `frames_raw`,
`hero_crops.html`, `labels.json`, candidate templates and candidate reports,
then prints the **exact next command** for whatever is missing. It never
writes to the DB, never edits layouts, never promotes comps — safe to run any
time. Add `--next-step-only` for just the recommendation, or `--json` for a
machine-readable report.

---

## 2. Exact command order

Run these in order; the doctor will point you to the right one if you skip or
land mid-way. (Confirm the script names match your repo — see "Adjusting
paths" below.)

```
1. py pipeline\video_pipeline.py --demo
      -> initializes data\owcs.sqlite and scaffolds folders (fixture demo)

2. py pipeline\run_owcs_auto.py --source <source_id> --start H:MM:SS --end H:MM:SS --every 30
      -> downloads one clip for the window, extracts frames into the run folder

3. py pipeline\build_layout_debug.py --run <run_id> --layout <layout.json> --from-frames reports\auto\<run_id>\frames_raw
      -> draws the 10 HUD boxes on real frames so you can verify the layout

4. py pipeline\build_crop_report.py --run <run_id> --layout <layout.json>
      -> cuts the 10 hero crops per frame into hero_crops.html

5. python -m http.server 8000
      -> then open http://localhost:8000/reports/auto/<run_id>/hero_crops.html
         and label crops (this is what fills data\eval\labels.json)

6. py pipeline\build_hero_templates.py --from-labels data\eval\labels.json --out templates\candidates
      -> exports CANDIDATE templates (not real templates) from your labels

7. py pipeline\eval_detection.py --labels data\eval\labels.json --templates templates\candidates
      -> measures candidate accuracy; writes a candidate report

8. py pipeline\eval_detection.py --templates templates\candidates --run <run_id> --dry-run
      -> read-only dry-run detection; nothing is written to the DB
```

After step 8 the doctor says **"ready for future template approval"** — that
approval is a deliberate, manual step you do later, not part of this pass.

---

## 3. What each report means

| Page / file | What it shows | You look at it to… |
|---|---|---|
| `reports\auto\<run_id>\index.html` | Per-run summary: params, frame count, step status | Confirm the run finished and see what it did |
| `reports\auto\<run_id>\layout.html` | Real frame with the layout's 10 HUD boxes drawn on top | Verify the boxes sit on the hero portraits |
| `reports\auto\<run_id>\hero_crops.html` | The 10 cropped hero slots per frame | Eyeball crop quality and label heroes |
| `data\eval\labels.json` | Your hand labels (which hero is in each crop) | Ground truth for templates + eval |
| `templates\candidates\` | Candidate hero templates cut from labelled crops | Inputs to eval — **not** the approved templates |
| `reports\candidates\<run_id>\` | Candidate eval + dry-run detection output | See measured accuracy and per-slot scores |
| `next_step.html` (optional, from `--emit-banner`) | The doctor's recommendation as an HTML snippet | A reminder you can link from a report page |

Detection-row labels you'll see later: **high** (all 10 slots confident),
**needs-review** (any slot low), **no-detection** (skipped / below floor).
Nothing counts toward stats unless it's `reviewed` or `auto-high`.

---

## 4. What NOT to do yet

- **Don't promote comps.** No `promote_detections.py`, nothing writes
  `source="cv"` rows to the DB this phase.
- **Don't run a full 6-hour VOD pass.** Only short calibrated windows
  (2–5 min).
- **Don't overwrite real templates.** Everything goes to
  `templates\candidates\`. The approved `templates\` set is left alone.
- **Don't hand-edit `labels.json`** — create it by labelling in
  `hero_crops.html`.
- **Don't touch FACEIT / team-side logic**, and remember FACEIT is match
  facts only — it never infers comps.
- **Don't change the detector or thresholds** while calibrating inputs.
- **Don't OCR** yet.

---

## 5. Adjusting paths / commands

The doctor keeps every path and every printed command in one `CONFIG` block at
the top of `pipeline\owcs_doctor.py` (`PATHS` and `COMMANDS`). If a script name
or folder in your repo differs from the defaults above, edit that block once —
the checks and recommendations update automatically. The doctor only *prints*
these commands; it never runs them.
