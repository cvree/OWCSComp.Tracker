# Ingest the CR vs ZETA Korea Grand Final — step by step

This walks you through turning the ObsSojourn POV video
(`https://www.youtube.com/watch?v=is7eHd0nf84`) into real data on your live
site. It assumes **no prior knowledge**. Every command is copy-paste. Do the
parts in order; each one explains what it does and what "good" looks like.

**What's already done for you (in the repo):** the match record, all six map
windows (from the video's chapter timestamps), the new map "Neon Junction",
and the video registered as a source. You do NOT need to type any of that —
it's committed. You start at Part 0.

You will be typing into a **terminal** (a text window where you run
commands). On Windows that's "PowerShell" or "Command Prompt"; on Mac it's
"Terminal". Open it, then in it go to the project folder — the folder that
contains `pipeline/`, e.g.:

```
cd path/to/OWCSComp.Tracker
```

---

## Part 0 — one-time computer setup (do once, ever)

The tracker needs three free tools installed on your computer:

- **Python** (runs the pipeline) — https://www.python.org/downloads/ (tick
  "Add Python to PATH" during install on Windows).
- **ffmpeg** (cuts the video into frames) —
  https://ffmpeg.org/download.html (Windows: use the "gyan.dev" build and add
  its `bin` folder to PATH; Mac: `brew install ffmpeg`).
- The Python libraries the tracker uses. In the project folder, run:

```
pip install -r requirements.txt
pip install yt-dlp
```

To check it all worked (each should print a version, not an error):

```
python --version
ffmpeg -version
yt-dlp --version
```

If any says "not found", that tool isn't on PATH yet — re-do its install step.

---

## Part 1 — tell it Neon Junction's game mode (10 seconds)

Neon Junction is a new map, so it was added with mode "Unknown". Set the real
mode (you know it from watching — Control / Escort / Hybrid / Push /
Flashpoint / Clash). Example if it's Control:

```
python pipeline/set_map_mode.py --map neonjunction --mode Control
```

*(If that helper doesn't exist yet, you can skip this — it only affects how
the map is labelled, not the hero reading.)*

---

## Part 2 — download the video (~10–20 min, once)

This saves the video to your computer so ffmpeg can read frames from it:

```
yt-dlp -f "bv*[height<=1080]+ba/b[height<=1080]" -o "work/clips/krgf.mp4" "https://www.youtube.com/watch?v=is7eHd0nf84"
```

When it finishes you'll have `work/clips/krgf.mp4`. (If it stalls, see
`docs/windows-setup.md` — the pipeline has fallbacks, but a plain full
download is simplest.)

---

## Part 3 — teach the tracker to read THIS video's HUD (once per channel)

Every ObsSojourn video uses the same on-screen scoreboard bar, so you
calibrate it **once** and reuse it for all their videos. Calibration looks at
a handful of gameplay frames and figures out exactly where the ten hero
portraits sit on screen.

Pick 6–8 timestamps that are clearly mid-fight (not menus/replays). Good ones
from the map windows: `120, 400, 900, 1500, 2000, 3000` (seconds). Then:

```
python pipeline/calibrate_source.py --clip work/clips/krgf.mp4 --times 120,400,900,1500,2000,3000 --source-id owcs-is7ehd0nf84 --out layouts/obssojourn_pov.json
```

Then **look at the result**: open `reports/calibration/owcs-is7ehd0nf84/sheet.png`.
You want to see a box drawn tightly around each of the ten hero portraits
(five per team, top bar). If the boxes are off, adjust the times (use
clearer combat frames) and run it again. Green/confidence ≥ 0.55 means it's
good enough to proceed.

You can watch this health readout live in the browser too: run
`python pipeline/serve.py`, open `http://localhost:8000/calibration.html`.

---

## Part 4 — harvest the hero pictures (once per broadcast)

This cuts small portrait crops for the heroes that appear, so the reader has
templates to match against:

```
python pipeline/harvest_templates.py --clip work/clips/krgf.mp4 --times 120:4160:15 --layout layouts/obssojourn_pov.json --out templates/owcs_is7ehd0nf84 --cluster
```

It groups similar crops; you label which hero each group is (the video's
"Heroes played: Mizuki, Juno, Jetpack Cat, Lucio" and the on-screen names
help). Save your labels, then re-run with `--labels <yourfile> --variants 5`.
This is the one genuinely hands-on step; take your time — good templates are
what make the reads accurate. (Full detail: `HANDOFF.md` → "Repeatable
workflow for a NEW VOD/map", step 3.)

---

## Part 5 — read every map (ONE command)

Now the payoff. All six map windows were already worked out from the video's
chapters and stored in the database, so you don't type them — one
orchestrator command reads them and runs every map for you.

**Do a dry run first** (no `--write` — reads and prints a summary, saves
nothing):

```
python pipeline/ingest_obssojourn.py --clip work/clips/krgf.mp4 --match m-cr-zeta-krgf
```

It prints, per map: how many frames it counted vs skipped (and why), the
rounds it found, and any comps it resolved. Read that and sanity-check it
looks reasonable. When you're happy, run it **for real** by adding `--write`:

```
python pipeline/ingest_obssojourn.py --clip work/clips/krgf.mp4 --match m-cr-zeta-krgf --write
```

That saves every map's comps to the database. (Options: `--maps 1,2` does just
those maps; `--plan-only` prints the six underlying commands without running
them; add `--ocr-guard` at the end to also read team names + bans for match
confirmation.)

The vision upgrades all run automatically inside this: it only reads settled
combat (skipping setup, the first seconds of each round, the last 10 s, and
any replay/highlight — all bookmarked in each map's `phases.json`), uses the
1-Tank/2-Damage/2-Support rule to resolve tricky slots, and cross-checks the
team names/bans against the match.

---

## Part 6 — review, publish (auto-deploys)

Look at the evidence report for each map (the ingest prints its path, e.g.
`reports/ingest/krgf-m1/review.html`) — every confirmed comp and swap has a
crop you can eyeball. Fix anything wrong with the corrections workflow
(`HANDOFF.md`). When happy:

```
python pipeline/export_data.py --public
git add -A && git commit -m "Ingest CR vs ZETA Korea Grand Final (ObsSojourn POV)" && git push
```

That push **auto-deploys** — within a couple minutes the match, its maps,
comps and pick-rates appear on your live site (Matches, the CR and ZETA team
pages, the Maps showcase, and Stats), no extra steps.

## If you have a second POV of the same match
Ingest it exactly the same way but with its own `--source-id` and
`--ingest-id`. Because both share the match signature
`crazyraccoon--zetadivision--2026-07-12`, they attach to the **same match**
and their reads **cross-confirm** (agreeing comps get more confident) instead
of duplicating.
