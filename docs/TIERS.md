# Hero tiers (`tiers.html`)

S–D hero and comp tier lists per map, built from the community
spreadsheet, for any **viewing date** and **date range**, with the team
power ranking that decides how much each win is worth.

Reached from Comps (`stats.html`, `meta.html`) and the search palette. It is not
a separate nav slot.

## What the page shows

| Tab | What |
|---|---|
| Heroes | Per map (or all maps): Tier S → D tables, each hero with pick share, raw win, **adjusted win**, **value**, sample, and an auto-written line (biggest win, worst loss, most-run comp, teams, ban rate). Heroes under the sample floor are under "Low sample". |
| Comps | First-listed five-hero comps per map, tiered across the map's comps. |
| Power ranking | Every team's power as of the viewing date: Elo + standings, movement since the range start. |
| How it's scored | The formulas and every weight, read live from the data file. |

Controls (all kept in the URL): viewing date (`asof`), range (`range` = 14/30/60/90/180/season/custom,
`from`), region, role, map, tab. "Jump to" buttons set the range to one stretch of the
season (Stage 1, Champions Clash, …).

## Dates are estimates

The spreadsheet records **no dates**: only region + stage (its tab) and
`Week N | Day N | Game N` (its card). `config/external/sheet_schedule.json`
maps that position to a calendar date: `weeks["N"]` is Day 1 of week N,
Day D adds D−1 days, and LANs list their `days`. Each stage notes its
`source` and `confidence` (`window` = the stage's dates were published; `estimate` =
guessed). Pacific Stage 2 and the Pre-Season Bootcamp are `estimate`. The page
says "estimated" wherever a date appears. These dates live only in
`sheet_tiers.v1.js`. They are never written into the imported records, and
`meta.html` still treats the sheet as undated.

## The value of a win

```
importance = event weight × bracket weight
expected   = 1 / (1 + 10^((power_opp − power_team) / 400))   ← power just BEFORE the map
value      = importance × (result − expected)
adjusted win (hero) = 50% + Σ share·value / (Σ share·importance + shrink)
```

* **Event weight**: regional stage 1.0, Asia Stage 1 championship 1.25,
  Champions Clash 1.4, Midseason 1.5, World Finals 1.6, Pre-Season 0.6.
* **Bracket weight**: matched on the card's format text: Grand Finals 1.5,
  Upper Finals 1.35, Lower Finals 1.3, Semifinals 1.25, Third place / Quarterfinals 1.15, LCQ /
  Seeding / Round 1 1.05, Round Robin / Swiss / Group stage 1.0; Midseason's
  "Group A/B …" rounds × 0.9.
* **Power** = Elo + standings. Elo starts at the team's region tier
  (Korea 1600, NA/EMEA 1550, China 1525, Japan 1450, Pacific 1425) and moves
  by `32 × importance × (result − expected)` after every map, so international
  results settle cross-region strength. Standings add up to ±75 points for the team's
  map record in its current regional stage (shrunk toward 50%).
  Ratings always replay the whole season up to the viewing date, whatever the range or region filter.
* **Share**: a player's listed heroes count by order (1 → 1.0; 2 → 0.7/0.3; 3 →
  0.6/0.25/0.15). The sheet lists heroes, not time played.

## Tiers

Within each role on each map:
`score = 50 × √(pick share ÷ most-picked in the role) + 50 × (clamp((adj − 50%) / 10%, −1, 1) + 1) / 2`.
S ≥ 72, A ≥ 58, B ≥ 44, C ≥ 30, else D. A hero on under 2% of the team-maps in view
(or under 1.5 weighted listings) is "low sample" and gets no tier. Comps need 3 maps.

Every number is in `config/tier_weights.json`. Change it, then run:

```bash
python3 pipeline/sheet_sync.py rebuild
```

## Files

| path | what |
|---|---|
| `config/external/sheet_schedule.json` | stage/week/day → estimated date, with sources |
| `config/tier_weights.json` | every weight and threshold above |
| `pipeline/external_sheet/tiers.py` | builds `assets/data/sheet_tiers.v1.js` (part of every sheet export; `sheet_sync.py check` gates drift) |
| `assets/js/app/tierstats.js` | the maths (pure; browser + Node) |
| `assets/js/app/page-tiers.js`, `tiers.html` | the page |
| `pipeline/test_tier_stats.py` | dates, list order, disputed winners, Elo, upsets, as-of, shrinkage, real data |
