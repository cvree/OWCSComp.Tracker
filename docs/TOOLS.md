# The tools: Draft Lab, Versus, and hero partners

Three tools built on the community spreadsheet's pro line-ups. The maths for all
three is `assets/js/app/lab.js`, which is pure and tested by `pipeline/test_lab.py`.
All three follow the site-wide time range (14 days by default).

## Why these three

Before building, we surveyed what Overwatch and other esports stats sites offer,
and what their users rely on:

- **Draft assistants** are among the most-used paid features in Dota 2: Dota Plus
  recommends picks from win rates and accounts for the enemy draft.
  BecomeTheGamer scores drafts on hero synergy and advantage.
- **Counters and synergies.** CounterWatch's per-hero "best with / best against"
  tables, ranked by a shrunk win rate, are the core of the main surviving
  Overwatch stats site. Overbuff and OmnicMeta have both shut down.
- **Team head-to-head pages** lead with rating, recent form, a model's Bo3/Bo5
  chances and every past meeting, and fans use them before every match.
- **The OWCS ban rules** set the draft's shape. Each team bans one hero per map,
  the two bans must be from different roles, and a team cannot repeat its own ban
  within a series.

Pick'em contests came up too. They need accounts, leaderboards and prizes to
work. The honest single-player version is the model's odds on Versus, which
this site can stand behind.

Sources: [CounterWatch, best Overwatch stats apps in 2026](https://www.counterwatch.gg/blog/best-overwatch-stats-apps-in-2026-what-s-left-after-overbuff-eb06b9f0),
[CounterWatch counter and synergy tier lists](https://www.counterwatch.gg/faq/how-do-the-counter-and-synergy-tier-lists-work),
[Dota Plus features explained](https://esports.gg/opinion/dota-2/is-dota-plus-pay-to-win-every-paid-dota-2-feature-explained),
[BecomeTheGamer](https://esports.inquirer.net/13476/become-the-gamer-a-web-tool/amp),
[a Dota 2 head-to-head methodology](https://www.dota2protips.com/methodology/head-to-head),
[OWCS ban rules at EWC 2026](https://esportsworldcup.com/en/news/owcs-msc-at-ewc-26-survival-guide),
[Dot Esports on the role rule](https://dotesports.com/overwatch/guides/overwatch-ewc-2026-schedule-results-standings),
[Esports World Cup Pick'em](https://rdy.gg/en/news/esports-world-cup-launches-200-000-fantasy-league-and-pickem-predictions).

## Draft Lab (`draft.html`)

Pick a map (optional), an opponent (optional), the two bans, and heroes for either
side. Everything below updates as you click.

| Panel | What it is |
|---|---|
| **Pro comps with your picks** | Finished five-hero comps that contain every hero you picked and none that are banned, with W–L, record against a line-up holding the other team's picks, and who ran them. *Use this* fills your line-up. |
| **Fill the next slot** | For each role still open (1 tank, 2 damage, 2 support), the heroes whose line-ups with all your current picks did best. Tap one to add it. |
| **What has beaten their line-up** | Comps that faced a line-up containing the other side's picks, best record first. |
| **Bans to consider** | The most-banned heroes here, and, with an opponent chosen, what that team plays most. Tap one to make it your ban. |

- **A line-up** is a side's complete first-listed comp. Without one, it is the
  heroes the players are listed with first (weight ≥ 0.5).
- **Ranking:** a shrunk win, `(wins + 3) / (maps + 6)`, times a confidence factor,
  `min(1, sqrt(maps / k))`, so a 2–0 cannot outrank a 16–11.
- **Thin maps:** if the map has fewer than 30 line-ups in the range, the lab uses
  every map and says so, offering a wider window in one tap.
- **The URL holds the draft** (`?map=&us=&them=&b1=&b2=&opp=`), so *Copy link*
  shares it exactly. *Surprise me* draws one of the five best comps.
- **Search:** typing in the search box filters the hero grid, and Enter picks the
  first match.

## Versus (`versus.html`)

Any two teams (`?a=&b=`); the default is the two strongest teams right now.

- **The model, per map.** The Elo expectation from both teams' power at the end
  of the range: the Hero tiers page's power ranking, seeded by region and moved
  by every map, plus current-stage standings.
- **Series odds.** Bo3, Bo5 and Bo7 are the chance of reaching ⌈n/2⌉ map wins
  first: `Σ_{j<k} C(k−1+j, j) · pᵏ · (1−p)ʲ`.
- **Map by map.** Each team's record on the map. The map's odds nudge the overall
  chance by the difference in the two teams' shrunk records there:
  `logit(p) + 1.2 · (shrunkA − shrunkB)`. A 1–0 map barely moves it.
- **Also shown:** form (last ten maps), every past series between them (whatever
  the range), what each runs (the comp book), and what each plays, bans and
  gets banned.
- The page labels all of this as a model, not a promise.

Every team page links to *Compare with another team* and *Draft against them*.

## Best with, hardest against (`hero.html`)

For the hero's side on every map in range:
- **Best with:** teammates with the best shrunk record.
- **Played most with:** the usual partners.
- **Hardest against / best against:** enemy heroes it does worst or best into.

A pair needs at least four decided maps together to be listed, and every row
keeps its raw W–L.

## The hero catalogue guard

`test_lab.py` fails if the spreadsheet lists any hero 50 or more times that the
catalogue does not know.

D.Mon and Doctrine were that case. For weeks, 1,518 listings were dropped, and
most recent line-ups counted as incomplete. In the last 14 days, 76 of 398 sides
had a full comp before the fix and 394 after. The guard makes the next new hero a
red build instead of a quiet hole in the recent meta. The fix itself is the
procedure in `docs/EXTERNAL-SHEET.md`.
