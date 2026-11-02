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
| One signal colour | Gold: a published fact, the one primary action, the brand mark |
| One light source | The faint warm wash behind the header. Static. It never moves. |
| One transition language | Content rises once as it arrives, and can never withhold itself |
| A few micro-interactions | Button lift, the map story's playhead, a hero's ring when it swaps in |

## The rules

### 1. Gold is spent, not sprinkled

*From Bellwether:* "Gold appears once. It is the early sign. If it
starts marking buttons and links it stops meaning anything, and the icon
loses the thing that makes it legible at 40 pixels."

The tracker's own stylesheet already said gold meant *published*. In
practice the phone home page drew gold about 160 times: every ▶ Watch
button (77 of them, each styled as the primary action), every map
number, every link, every winner, every data bar, every selected tab.
The one map in the feed that actually has a published comp was outlined
in gold too, and among 160 other gold marks it could not stand out.

Gold now marks three things:

- **a published fact**: the published chip, a published broadcast on
  the archive calendar, a map with a published comp, a swap on a
  published timeline;
- **the one primary action on a screen**, such as "Submit a game" or
  "Play the map". An action repeated on every row is not primary, by
  definition;
- **the brand mark**.

Everything that used to borrow gold now has a neutral token of its own:

| Token | Job |
| --- | --- |
| `--link` | A link. Told apart by its underline, not by colour. |
| `--win` | A result. The winner of a map is bright, not gold. A result is not a status. |
| `--bar` | A quantity. Every meter, every data bar. |
| `--outline` (`--sel`, `--wash-sel`) | **The Contenders lime**, `#9bec44`. Every "this one": the keyboard focus ring, the chosen option, the current row, the playhead. |
| `--tier-s` … `--tier-d` | Tiers, as one diverging ramp around a neutral B. |

`GOLD_ALLOWED` in the test is the list of selectors allowed to spend
gold. Adding to it is a design decision, not a lint fix.

### 1b. The outline is the site's own colour

Gold is the brand's signal, and lime is the brand's hand: an original
green in the spirit of Overwatch Contenders that marks wherever the
reader *is*. Focus used to be cyan, which already meant "processing
right now", and selection was plain white, which said nothing. One
lime outline now does both jobs. It sits at OKLCH hue 132, on the
yellow side of green, so it is never read as emerald (evidence, hue
160) or as the Support role. The test requires it to stay 15° from
both and from gold.

### 2. Distinct meanings get distinct colours, by measurement

*From Bellwether:* both themes are contrast-audited in CI, and the
severity ramp picks its own label colour by luminance.

Two things were wrong and only measuring showed them:

- **Amber ("needs a person") was gold ("published").** `#e8a33d` and
  `#f3a72f` are 72.9° and 73.1° in OKLCH hue, a fifth of a degree
  apart, so the two most important states in the product were one
  colour. Amber is now `#ef8448`, OKLCH hue 48: halfway between red and
  gold, and lighter than red. The test requires 15° between any two
  states that mean different things.
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
