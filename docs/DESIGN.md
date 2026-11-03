# Design principles

The tracker shares a designer with two other products, and they have
each worked out, the hard way, a handful of rules that are not about how
they look. **Manifester** (an affirmation player) and **Bellwether** (a
private symptom journal) look nothing like a broadcast analytics desk:
one is a twilight garden in rose and sage, the other is warm graphite
and a notebook's paper. Neither palette belongs here, and neither is
borrowed.

What is borrowed is the discipline underneath them. This file is that
discipline, translated into the tracker's terms. `pipeline/test_site.py`
(`test_design_rules`) holds the parts that can be measured, so a later
pass cannot quietly undo them.

## The thesis

Manifester wrote its whole first-minute brief as one sentence and spent
its motion budget against that sentence and nothing else. The tracker's:

> **A quiet instrument that lets the broadcast supply the colour —
> team marks, hero portraits, scores — and spends its own colour only to
> say what is known, what is proven, and what still needs a person.**

If a change makes the interface louder than the heroes on it, it is the
wrong change.

| Budget | Spent on |
| --- | --- |
| One accent colour | A classy lime: a published fact, the one primary action, the brand mark, the headline's phrase, a faint edge on hover |
| One light source | The faint wash behind the header. Static. It never moves. |
| One transition language | Content rises once as it arrives, and can never withhold itself |
| A few micro-interactions | Button lift, the map story's playhead, a hero's ring when it swaps in |

## The rules

### 1. The accent is a classy lime, and it is spent, not sprinkled

*From Bellwether:* "Gold appears once. It is the early sign. If it
starts marking buttons and links it stops meaning anything, and the icon
loses the thing that makes it legible at 40 pixels."

**The colour.** The accent is `--accent: #c7e770`, which is
`oklch(0.88 0.15 122)`, with `--accent-hi: #d6f290` for text on a wash
and `#7c9529` as the deep end of the brand mark's gradient. It replaced
the warm OWCS gold (`#f3a72f`). "Classy" here means a specific thing:
high lightness and restrained chroma, so it reads as a considered
chartreuse rather than a highlighter. It sits beside deep ink
(`--accent-ink: #0e1206`) on a primary button at 14:1, and it is 13:1
as a mark on a card. Six candidates were rendered side by side on the
real surfaces. The brighter ones read as neon, and the duller ones
read as olive.

It is kept 38° of hue from emerald and the Support role, so
"published" is never read as "evidence". The test holds that gap.

**The discipline.** The stylesheet already said the accent meant
*published*. In practice the phone home page drew it about 160 times:
every ▶ Watch button (77 of them, each styled as the primary action),
every map number, every link, every winner, every data bar, every
selected tab. The one map in the feed that actually has a published comp
was outlined in it too, and among 160 other marks it could not stand
out.

The lime now marks three things:

- **a published fact**: the published chip, a published broadcast on
  the archive calendar, a map with a published comp, a swap on a
  published timeline;
- **the one primary action on a screen**, such as "Submit a game" or
  "Play the map". An action repeated on every row is not primary, by
  definition;
- **the brand mark**: the CT tile and the favicon.

And it lends the page two quieter touches, both chosen deliberately
rather than leaked:

- **the headline's one phrase** ("What's winning *right now*"), in
  `--accent-hi`. That is the page's single brand moment, and it is a
  phrase, never a highlighter block;
- **a faint lime edge when you hover a card**: a map, a game, a team
  tile or a hero portrait. It is `--accent-line`, 42% alpha, so a
  hovered card is still told apart from the map with a published comp,
  which carries its lime edge at rest. The S-tier hero cards keep their
  own role-coloured glow, because that glow is the heroes' personality.

Everything that used to borrow the accent now has a neutral token of
its own:

| Token | Job |
| --- | --- |
| `--link` | A link. Told apart by its underline, not by colour. |
| `--win` | A result. The winner of a map is bright, not lime. A result is not a status. |
| `--bar` | A quantity. Every meter, every data bar. |
| `--outline` (`--sel`, `--wash-sel`) | Bone. Every "this one": the keyboard focus ring, the chosen option, the current row, the playhead. Neutral so that "where you are" is never confused with "published", and so focus stops borrowing cyan, which means "working". |
| `--tier-s` … `--tier-d` | Tiers, as one diverging ramp around a neutral B. |

`ACCENT_ALLOWED` in the test is the list of
selectors allowed to spend the accent. Adding to it is a design
decision, not a lint fix. The old `--gold` tokens are now `--accent`.
The `*--gold` modifier classes and `data-accent="gold"` remain as
aliases so existing markup keeps working, and they draw lime.

The operator pages (`control-room.html`, `setup.html`) use their own
stylesheet, `appliance.css`, where the accent was also used for
warnings, the Damage dot and "detected" badges. Those three now have
their proper colours (orange `--warn`, red and indigo) rather than
turning lime, which would have made them read as published.

### 2. Distinct meanings get distinct colours, by measurement

*From Bellwether:* both themes are contrast-audited in CI, and the
severity ramp picks its own label colour by luminance.

Two things were wrong and only measuring showed them:

- **Amber ("needs a person") was the old gold ("published").**
  `#e8a33d` and `#f3a72f` are 72.9° and 73.1° in OKLCH hue, a fifth of
  a degree apart, so the two most important states in the product were
  one colour. Amber is now `#ef8448`, OKLCH hue 48: 25° from red and
  lighter than it, and 74° from the lime accent. The test requires 15°
  between any two states that mean different things.
- **`--tx-3` claimed 4.7:1 and measured 4.29:1.** It is the colour of
  every caption, unit and helper line, so it failed AA across most of
  the site. It is now `#828a96`: 5.3:1 on a card and 4.7:1 on the
  deepest inset. The test checks all three inks on all five surfaces.

Bellwether's ink rule applies unchanged. Three readable weights, and the
hierarchy between them comes from size, weight and capitals, which
survive a contrast fix. A lighter grey does not survive one.

### 3. Nothing is smaller than the thumb

*From Bellwether 1.34:* "`--fhj-tap` is 44px and it is a floor in
*both* directions — the earlier audit checked height and let width
through." And: "jsdom has no layout and would have passed the old code
forever."

Measured in a real browser at 390px, the tracker's phone layout had
148 team names 28px tall, header buttons 34×30, every filter pill 30px,
every "Full tier list →" 20px, and, on the Meta explorer, every filter
`<select>` **31px wide**. That last one was not a sizing choice. It was
a bug: a later "team wall" section reused the form-field class name
`.field` as a 132px grid, and the cascade turned every labelled filter
on the site into a sliver. The team wall is `.teamwall` now, and the
test requires `.field` to be declared once.

Under `@media (pointer: coarse)`:

- controls that can simply grow, grow: buttons, inputs, segmented
  options, tier map pills, team rows and footer links all reach 44px;
- links whose ink must stay small, such as a section link, the dataset
  line or the brand, take the tap on an invisible 44px box centred under
  them, so nothing moves and everything lands;
- two things are exempt, and both have a full-size route to the same
  place: a hero portrait inside a five-hero comp strip, and the marks on
  a map timeline.

It is scoped to coarse pointers on purpose. The desk view is a dense
analytics surface, and a mouse can aim at 28px.

### 4. A row that scrolls sideways says so

*From Bellwether:* the trend metric picker is "a single tab stop … the
selection is always scrolled into view, edge fades and arrows show that
more exist."

On a phone the region filter ended at "Japa", which read as the last
region rather than the middle of the list. Every `.seg` that overflows
now fades on whichever end has more behind it (`shell.js`,
`initScrollRows`), and the chosen option is scrolled into view when the
row is drawn, so "Pacific" selected is never "Pacific" hidden.

### 5. The tab you are on takes you to the top

*From Bellwether:* "Tapping the tab you are already on returns to the
top of it. A year of History is a very long page, and the alternative is
the status bar — the one part of a phone a thumb cannot reach."

The phone home page is 28,000px of maps. Pressing **Maps** while you are
on Maps now scrolls to the top (through Lenis when it is running)
instead of reloading the page and losing your filters. Only an exact
match is intercepted. A link that would change the query still
navigates.

### 6. Say it once

*From Manifester:* "Say the app's name, and stop saying everything else
twice."

The Comps page set the spreadsheet's four headline numbers under a
heading reading **SPREADSHEET IMPORTED · The community spreadsheet,
counted separately**, then put a **SHEET** badge on each of the four
tiles, then opened the coverage card below them with **SPREADSHEET
IMPORTED** again. That is one fact stated six times. Provenance is the
product's whole credibility, and repeating it until it is wallpaper is
how that credibility gets worn down. A label set over a group is now not
repeated on its members. A tile or table that stands alone still
carries its own badge.

### 7. Motion makes it feel faster, or it goes

*From both:* Manifester's "every motion is interruptible, nothing is
delayed to show an animation". Bellwether moved its overlays out of the
entrance stagger because "motion has to make the app feel faster, and
that was the one place it did the opposite". Both removed Vanta for the
same reason.

The tracker had already arrived here independently (see the header of
`motion.js`), so this pass keeps the rule rather than changing it. It is
listed so that the next proposal for a particle field has something to
argue with.

### 8. Honour what the reader's system asks for

*From Bellwether:* "`prefers-contrast: more` darkens text and card
borders."

Reduced motion was already honoured. `prefers-contrast: more` now lifts
`--tx-2` and `--tx-3` a step, turns hairlines into visible edges, and
strengthens link underlines. Nothing else changes.

### 9. Describe what it records, not what it concludes

*From Bellwether:* "Describe what it records, not what it concludes,"
which there is a legal position as much as a stylistic one.

The tracker already writes this way ("finding is not reading"; "a
discovered broadcast is a candidate, not a fact"). It is recorded here
because it is the same rule, and because it is the one most likely to
erode under a headline like "What's winning right now". A tier is a
weighted count of what teams picked and how those maps went. It is
never a claim about what is strong.

### 10. What you came for comes first

*From Manifester's first minute:* "the alternative, an empty text box
and a Start button, is the worst possible first impression of an app
whose entire value is the words it already has". The tracker's value is
the comps it already has, and its pages had drifted into opening with
everything else.

- **Content before provenance.** On a phone the Comps page used to show
  four broadcast-coverage tiles, the spreadsheet's tiles, a coverage
  table and two link blurbs before the first comp, which sat about
  2,500px down. The comps now come straight after the heading, and the
  coverage sits closed at the bottom under **About these numbers**.
  Provenance is still one tap away: the ⓘ badge beside every list, and
  the section at the foot of the page.
- **Every page leads with its answer.** A team page opens with *what
  they run* and a hero page with *the comps built around it*. Each comp
  is one tap from its full record (`stats.html?…&cc=<comp>` opens it
  expanded). The broadcast feed, which mostly says "comp not read yet",
  comes after the record, not before it.
- **Operators' questions stay with operators.** The dataset status strip
  ("77 maps indexed · 1 with comps") sat above a page listing 1,306
  comps. It, the "unmapped" registry marks and the Matches source filter
  now show in admin mode only (`html.is-admin`).
- **A list is a page, not the season.** Matches renders 40 at a time,
  latest first, instead of one 73,000px page in the spreadsheet's oldest-
  first order. The home page's broadcast feed shows the latest two
  broadcasts and pages the rest.
- **Filters by how often they are asked.** The comp book keeps map, hero,
  region and sort in view. Window, stage, mode, team and the minimum fold
  under **More filters**, which opens by itself, with a count, whenever
  one of them is in use, so a filter is never hidden while it changes
  the list.
- **The nav names the questions.** Meta · Comps · Tiers · Teams ·
  Matches. Tiers and Matches had no nav entry, so most visitors never
  found them.

### 11. Time is the first control

The question this site answers is *what is winning now*. A season average
answers *what was winning on average since February*, which is a different
and much less useful question. So:

- **Every count of comps or heroes has a time range**, and it is the same
  range on every page: one bar, pinned under the header, never more than a
  tap away. The default is the **last 14 days**.
- **The range is always visible and always editable**: buttons for 7, 14,
  30 and 90 days, and a "Custom…" menu with the last 24, 48 and 72 hours,
  60 days and your own dates. There is no whole-season or per-stage range:
  nobody opens the site for February's meta. On a phone it is one row, so
  pinning it costs one row of the screen.
- **Region sits beside the dates** where a page filters by region (Hero
  tiers), so it stays in reach all the way down a tier list.
- **It travels.** Choose 30 days on Comps and the team page you open next
  is in 30 days too. Links carry it, so a shared view is the same view.
- **A short window is honest about being short.** Fourteen days in a quiet
  region can be a handful of maps. The page says so and offers the wider
  window in one tap, rather than widening it silently. The visitor chose
  "recent", and "recent" is what they get.

## Deliberately not borrowed

- **A light theme.** Bellwether treats light as first-class, not as an
  afterthought. A broadcast desk is watched in a dark room beside a
  stream, and a light theme done to Bellwether's standard is a second
  design system. Until someone asks for it, it is better not to ship one
  done badly.
- **The hue dial and the night light.** Manifester rotates its whole
  palette in OKLCH and lets you take the blue out of the light. Both
  make sense for something you fall asleep to. Here the hues are
  information, and a reader who rotated "published" into "blocked" would
  be misinformed.
- **Sound and haptics.** Bellwether's feedback layer answers a save on
  four channels. The tracker's public site has nothing to save, and its
  admin review keyboard is already fast without them.
- **An ambient backdrop.** Both apps have one. The tracker's is a single
  static wash, and its stylesheet says that if it ever competes with
  content it should go.
