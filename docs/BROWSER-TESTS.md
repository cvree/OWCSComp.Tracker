# The Chrome verification loop

The Python suite (`pipeline/test_*.py`) checks what the pipeline writes.
This checks what a person sees: the site, opened in a browser, used the
way people use it.

```bash
node tests/browser/run.mjs
```

That one command starts a static server on a free port (shaped like
GitHub Pages: files as they are, `404.html` for anything missing), opens
the site in Google Chrome, runs every check below, and writes:

| File | What it is |
| --- | --- |
| `tests/browser/output/report.html` | The readable report: every scenario and page, pass or fail, with its screenshots. Open this first. |
| `tests/browser/output/report.md` | The same, as text. CI puts it in the job summary. |
| `tests/browser/output/results.json` | The same, for a script. |
| `tests/browser/output/screens/` | One screenshot per page per width (the sweep). |
| `tests/browser/output/scenarios/` | One or more screenshots per scenario. |
| `tests/browser/output/traces/` | A Playwright trace for every failed scenario. `npx playwright show-trace <file>` replays it. |

It exits 1 if anything fails. `output/` is not committed.

## Setting up

Playwright is the only dependency, and only as a library:

```bash
cd tests/browser && npm ci
```

A global install (`npm i -g playwright`) works too; `lib.mjs` looks in
both places. No browser download is needed where Google Chrome is
installed.

## Which browser

Google Chrome, through Playwright's `chrome` channel. The report's first
line names the browser that actually ran, and it reads the browser's own
brand list to decide, because some machines put a Chromium or "Chrome
for Testing" build at Chrome's path. When that happens the run still
goes ahead and the report says **NOT Google Chrome**.

| Variable | Effect |
| --- | --- |
| `OWCS_REQUIRE_CHROME=1` | Not being Google Chrome is a failure. CI sets this. |
| `OWCS_BROWSER=chromium` | Use Playwright's Chromium without trying Chrome. |

## What it checks

**The sweep.** Every public page, plus one real team, hero and series
page, at 1440, 1024, 768 and 390 pixels wide:

- no uncaught exception, console error, failed request or 4xx/5xx
- the page does not scroll sideways (and which element caused it)
- no touch target under 24 by 24 pixels with another target inside its
  24px circle (WCAG 2.2, 2.5.8, measured the way the criterion is written)
- no broken image, no control without an accessible name
- there is an `h1` and the page is not empty

The one allowed failure is `/api/ping`. The site asks its own origin
whether a local tracker is running; on GitHub Pages it does not ask, and
on localhost a static server answers 404. That is the site working as
designed.

**The scenarios.** Each one drives a real workflow and asserts on the
DOM, not on how the page looks:

| Area | What is exercised |
| --- | --- |
| Navigation | every header destination, the phone menu, the 404 page |
| Keyboard | the skip link, a visible focus ring on every control tabbed to |
| Search | `/` opens it, a team is found and opened with Enter, nonsense says so |
| Home | the time range (and that it survives a reload), map search and its empty state, the VOD feed's team filter, Watch links carry a start time |
| Comps | filter, open a comp, reset, every tab, tabs restored from the address |
| Tiers | tabs, a region changes the sample |
| Meta explorer | only the open tab is drawn; filters redraw it |
| Teams | no org twice, HUD misreadings kept apart with their reason, the table's search / region / sort / empty state / address / reload |
| Versus | two teams, swap, the address carries both, no sideways scroll on a phone |
| Matches | a day that has passed is never "next up"; the same series before it starts is, with a countdown; no "Copy of" event; Schedule / Brackets / Results; region filter; following a team persists; times in the viewer's zone |
| Detail pages | team, hero, series, map, and an unknown id for each |
| Data | records add up (maps won equals maps lost across all teams), no duplicate names or ids, a winner is the side with more maps, no score beyond its best-of, no played series dated in the future, the footer states each source's age |
| Failure states | a data file that fails to load, every data file three seconds late, data two weeks stale, a 70-character name on a phone, a team with zero decided maps, reduced motion |
| Security | no credential-shaped string in anything served, a hostile team name is printed and never run |

## What the data is

The repository's own committed production data, unchanged. Nothing in
the suite invents a result.

Some scenarios need a condition the real data does not have today: a
file that will not load, a name far too long. Those change what the page
is given **in the browser, on the way to the page**, never on disk, and
their names start with `fixture:` so the report cannot be misread.

The page clock is frozen (`FROZEN_NOW` in `lib.mjs`), because the site
computes "upcoming", "awaiting", "stale" and every countdown from
today's date, and a check that depends on today is not repeatable. A
scenario that needs another moment sets its own (`{ clock: "…" }`).
Move `FROZEN_NOW` forward when the committed schedule has moved on far
enough that the frozen day no longer exercises anything.

## Why there are no pixel baselines

The data on these pages is rewritten several times a day by the
scheduled workflows. A committed screenshot of the home page would be
out of date by the next scan, and the only way to keep such a suite
green would be to re-record the baselines without looking, which is
worse than not having them. So the gate asserts on structure and
behaviour, and screenshots are written for a person to look at.

For a before-and-after of a visual change, compare two crawls:

```bash
git worktree add /tmp/before origin/main
node tests/browser/crawl.mjs --out /tmp/shots-before   # run from /tmp/before
node tests/browser/crawl.mjs --out /tmp/shots-after
```

`crawl.mjs` asserts nothing. It opens every page (admin pages included)
at every width and writes a screenshot and a `crawl.json` of everything
the browser noticed.

## Other commands

```bash
node tests/browser/run.mjs --grep teams      # only scenarios whose name matches
node tests/browser/run.mjs --no-sweep        # scenarios only
node tests/browser/run.mjs --trace           # keep a trace for every scenario
node tests/browser/perf.mjs --out after.json # load timings, median of 5, CPU slowed 4x
node tests/browser/perf.mjs --root /tmp/before --out before.json
node tests/browser/perf.mjs --compare before.json after.json
OWCS_LIVE_URL=https://<owner>.github.io/<repo>/ node tests/browser/live.mjs
```

`live.mjs` is the smoke check of the deployed site: real clock, real
network, and only the things that must hold on any day (every page
loads, has content, and its data is not stale). It is not part of the
gate because it needs the network and depends on the day.

## Adding a scenario

In `run.mjs`:

```js
scenario("teams: a short sentence saying what must be true", async (t) => {
  await t.go("teams.html");
  check(await t.page.locator("#tt-body tr").count() > 40, "the teams are listed");
  await t.shot("label");            // optional; one is taken at the end anyway
});
```

`check(condition, message)` and `eq(actual, expected, message)` fail the
scenario with the message. After the function returns, the scenario also
fails on any console error or bad response, unless it was declared
`{ expectErrors: true }`. Options: `viewport` (`desktop`, `laptop`,
`tablet`, `phone`), `clock`, `timezone`, `reducedMotion: false`.

Write the name as the thing that must be true. It is the line a person
reads in the report when it fails.
