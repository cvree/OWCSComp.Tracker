# Hero tiers (`tiers.html`) and the home snapshot (`index.html`)

## Home snapshot (`index.html`, `assets/js/app/page-home.js`)

Built for a high-rank / FACEIT player who wants the highest-value picks now.
Desktop first. Top to bottom:

1. **S tier right now**: per role, big hero cards with pick %, adjusted win %, ban %
   and a ▲/▼ pick trend against the window before. A-tier heroes are listed under
   "Also strong".
2. **Most banned**: ban rate bars (hover shows how the banning team did and who bans
   it most). Also the usual tank / damage / support ban and the most common ban pairs. Each
   team bans one hero per map and the two teams can't ban the same role. The pro data
   agrees: no hero banned by both teams, different roles on 99% of maps.
3. **Every map**: the most-run 5-hero comp (hover: record, swaps, teams, last seen),
   "Winning most" when a different comp has the better adjusted win, S-tier
   heroes, and the usual ban per role. If no line-up was run twice in the window,
   the card falls back to the last 60 days and says so.
4. **Comps that made noise**: the biggest wins (event × bracket × (1 − chance to
   win)), with an **UPSET** badge when the winner had under 40% to win.
5. The broadcast VOD feed (page-maps.js) is unchanged, at the bottom.

Window: the site-wide **time range** (see below), the last 14 days unless the
visitor picks another. The ▲▼ arrows compare with the window of the same length
just before. Region: All, NA, EMEA, Korea, China, Japan, Pacific, LANs (`?region=`).
Sources and methods sit behind one small "ⓘ Pro data" badge.


S–D hero and comp tier lists per map, built from the community
spreadsheet, for any **viewing date** and **date range**, with the team
power ranking that decides how much each win is worth.

Reached from Comps (`stats.html`, `meta.html`) and the search palette. It is not
a separate nav slot.

## What the page shows

| Tab | What |
|---|---|
| Heroes | Per map (or all maps): Tier S → D tables, each hero with pick share, raw win, **adjusted win**, **value**, sample, and an auto-written line (biggest win, worst loss, most-run comp, teams, ban rate). Heroes under the sample floor are under "Low sample". |
| Comps | First-listed five-hero comps per map, tiered across the map's comps; hover a comp for its record, swaps and teams. |
| Bans | Per role: every banned hero's ban rate and how the banning team did; the usual tank/damage/support ban on every map; the most common ban pairs. |
| Power ranking | Every team's power as of the viewing date: Elo + standings, movement since the range start. |
| How it's scored | The formulas and every weight, read live from the data file. |

Controls (all kept in the URL): the site-wide time range (`t`: 7 / 14 / 30 / 90 days,
or under "Custom…" the last 24 / 48 / 72 hours, 60 days or your own dates), region
(in the pinned range bar, beside the dates), role, map, tab. There is no whole-season
or per-stage range; old `?t=season`, `?t=stage:…` and `?range=season` links open the
default 14 days. Old `?range=` / `?asof=` / `?from=` links still open the window they
meant. The dates are per day, so "24 hours" is the newest match day in view.

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
| `pipeline/external_sheet/lp_dates.py` | a played series takes the day Liquipedia lists it on (same event, same pair, same score; `dc: "liquipedia"`); shared with `schedule_build.py` so Matches agrees |
| `config/external/sheet_schedule.json` | stage/week/day → estimated date, with sources, for series Liquipedia does not date |
| `config/tier_weights.json` | every weight and threshold above |
| `pipeline/external_sheet/tiers.py` | builds `assets/data/sheet_tiers.v1.js` (part of every sheet export; `sheet_sync.py check` gates drift) |
| `assets/js/app/tierstats.js` | the maths (pure; browser + Node) |
| `assets/js/app/page-tiers.js`, `tiers.html` | the tier page |
| `assets/js/app/page-home.js`, `index.html` | the home snapshot |
| `assets/js/app/tierui.js` | shared drawing: names, hover cards, comps, source badge |
| `pipeline/test_tier_stats.py` | dates, list order, disputed winners, Elo, upsets, as-of, shrinkage, real data |

## The comp book (`stats.html`, `assets/js/app/compbook.js`)

The Comps page reads the same export as a comp book: one row per distinct
five-hero comp (the five heroes a team's players are listed with first,
when all five are known), for any window, stage, region, map, mode, team
and hero. Each comp carries:

| Field | What |
|---|---|
| Played, share | Team-maps it was run on, and its share of every full line-up in view |
| Record | W–L and win %, plus maps with no winner recorded |
| Vs other comps | W–L with mirror maps left out. A mirror is one win and one loss for the same comp, so a comp that mostly meets itself sits near 50% whatever it is worth |
| Adjusted win, tier | The tiers page's weighting (event × bracket × opponent power, shrunk to 50% on small samples), graded across every comp in view |
| Maps, teams | Where it is run and who runs it, each with its own W–L |
| Against other comps | The opponent line-ups it met, with W–L |
| Bans around it | What the other team banned, and what its own team banned |
| Swaps | Heroes its players are also listed with (2nd / 3rd), order-weighted |
| Moments | Its most valuable win and costliest loss |
| Every game | Newest first, each linked to its series |

`pipeline/test_comp_book.py` holds the arithmetic on a synthetic season
and on the real export.

## The time range (`assets/js/app/timerange.js`)

One time range for the whole site. Every page that counts comps or heroes
(home, Comps and all its tabs, Hero tiers, team pages, hero pages) reads the
same range, shows the same bar pinned under the header, and redraws when it
changes. On a phone the bar is one row: a single menu holding every option.

| Preset | `?t=` | Window |
|---|---|---|
| **Last 14 days** (default) | *(none)* | the 14 days up to the newest map |
| Last 7 / 30 / 60 / 90 days | `7` … `90` | likewise |
| A stage | `stage:<id>` | the stage's first to last estimated day |
| Whole season | `season` | everything |
| Custom dates | `custom&tf=…&tt=…` | any from / to |

* **Relative windows count back from the newest map in view.** With a region
  chosen, that is the region's own newest map, so a region that has not played
  this week still shows its last 14 days of play rather than nothing.
* **The URL wins**, so a shared link shows what its sender saw. Otherwise the
  last range this browser chose is remembered (localStorage); a blocked store
  only means the default.
* **A thin window says so.** Under 20 maps, the page says the sample is small and
  offers the next wider windows in one tap. An empty window offers them in
  place of an empty table.
* **Dates are estimated** from the official schedule (above), and the bar says
  so beside the dates.

`pipeline/test_time_range.py` holds the rules: the default, each preset's dates,
region scoping, URL over memory over default, the old links, and that every
comp or hero page loads the module and pins the bar directly in `<main>`.

