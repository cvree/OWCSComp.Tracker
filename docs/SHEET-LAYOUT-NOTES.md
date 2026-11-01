# The real community sheet's layout (findings for the match-card parser)

Source: `sheet-sync` workflow runs 4–5 on branch `claude/charming-hamilton-hc7xxu`
(probe mode), 2026-10-31. Workbook: 52.8 MB, 41 tabs, 229 distinct pictures
placed 72,499 times. **Not a table**: every match is a fixed-size "match card".
The tabular importer (`layout.py` header detection) correctly found no headers
— run 3 failed `NO_DATA_TABS`, last good data kept.

Reproduce: Actions → sheet-sync → Run workflow on this branch with
`probe: true` (and optionally `probe_dump: "Japan - Stage 1!A1:CR53"`), or
locally `python3 pipeline/sheet_sync.py probe --xlsx FILE [--dump "Tab!A1:CR53"]`.
Logs are readable via the GitHub MCP `get_job_logs` (blob artifact downloads
are blocked from the Claude session; docs.google.com too).

## Tabs (41)
- `Landing Page`: credits. "Editor Contacts: @pthandley32" (G3), OWTV contacts
  (AD3); B1 = "Overwatch Champions Series 202…" (season year).
- Match-card tabs: `Pre-Season Bootcamp`, `{Japan,Korea,China,EMEA,North America,Pacific} - Stage {1,2,3}`,
  `Asia - Stage 1`, `Asia - Stage 3`, `Champions Clash Tokyo`,
  `Midseason Championship Paris`, `World Finals Guangzhou`. Tabs whose blocks have
  no teams yet (Pacific/Asia Stage 3, World Finals) = scheduled, not played.
- `Hero Index`: picture → hero name. Picture at (r, c), name directly BELOW at
  (r+1, c) (rows 3/5/7/9 pictures, 4/6/8/10 names). Row 2 has role icons with the
  label to the RIGHT: 3135baa3c0 = TANK, 030992f9af = DAMAGE, 9bd5d8fb03 = SUPPORT.
  Includes heroes not in our catalog (D.Mon, Doctrine…), so report them as unmapped.
- `Map Index`: picture → map name (map names are also text on the cards).
- `Stage N Hero Bans`, `Stage N Regional Maps*`, `Stage N LAN Maps`: the sheet's
  own cumulative TOTALS. These are summaries; re-derive from the cards, don't import.
- `Partner/Non-Partner/Old Team Index`: team logos and rosters (uses the card template).
- `Self-Check`: ground truth for validation. Row 2 headers; per row B=stage,
  D=region code (PRE, JP, KR, CN, EMEA, NA, PAC, ASIA, CC, …), E=total hero bans,
  F=map picks, H="Maps (Spreadsheet)", I="Matches (Spreadsheet)".
  e.g. Stage 1 JP: 32 matches, 116 maps, 232 bans.

## Match card geometry (block = 26 rows; blocks start rows 3, 29, 55, …)
Let b = block top row (the row with the event label in col C), c0 = column C.
The game label row is b+2 and matches
`^(Week \d+\s*\|\s*)?Day \d+\s*\|\s*Game \d+$`. Use it to find blocks.

Left panel (cols C..K):
- C[b] event ("OWCS Japan" / "Midseason Championship"); C[b+1] format/stage
  ("Round Robin [Bo5]", "Group B Upper Quarterfinals [Bo?]"); C[b+2] game label.
- Row b+4: C team-A code, E team-A logo IMG, G series score A, H series score B,
  I team-B logo IMG, J team-B code. (PNHB 3-1 TLM)
- Map list rows ≈ b+9 … b+20 (merged 2-row cells, so use origin cells): C "►" = team A
  won, D = A's map score, E map IMG, F map name, J = B's map score, K "◄" = team B won.
- C[b+21] "Player of the Match:\n<name>".

Map panels: one per map, panel k starts at column N + 17k (N, AE, AV, BM, CD).
Offsets are relative to (b, p):
- Row b+0: team-A players. Names at p+1, p+4, p+8, p+11, p+14; role icons at
  p, p+3, p+7, p+10, p+13. Player column spans start at [0, 3, 7, 10, 13], width to
  the next start, the last to 17. Observed order: DPS, DPS, TANK, SUP, SUP.
- Rows b+1, b+2: team-A HERO PICTURES. Each picture belongs to the player whose
  span contains its column. Up to 3 per player; order = (row, col).
- Row b+3: team-A name at p ("Please Not Hero Ban [0-0]": strip a trailing
  `[...]` record/seed such as "[KR #1]"); map name at p+10.
- Row b+5: map IMG at p+10. Row b+6: team-A logo at p, "PNHB Ban" at p+3, ◄ p+6, ► p+7
  (decorative), "TLM Ban" p+8. Row b+7: ban pictures at p+3 (A) and p+7 (B).
- Row b+11: team-B logo at p.
- Row b+13: winner text at p+10 ("Telomere Wins!").
- Row b+14: BAN TEXT: p+3 = team A's ban, p+7 = team B's ban (e.g. Mercy / Vendetta).
- Row b+15: team-B name at p.
- Row b+16: "Map: 2-0" at p+10 (A–B), "Series: 1-0" at p+13.
- Rows b+18, b+20: team-B HERO PICTURES (same player spans).
- Row b+22: team-B player names (p+1, p+4, …). Team-B role icons are different
  pictures (2703490da3 / b71bf5d1ea / b62b05504e), same position order.

Not in the sheet: calendar dates, who picked each map. Never infer either.
Order matches by (tab, week, day, game). Reconciliation with VOD maps must then
rely on teams + stage + map + map number (no date). Add: when either side has no
date, a stage mismatch rejects the candidate and a stage match scores +2.

## More findings (probe run 6, inventory runs 7–8, 2026-11-01)
- `Self-Check` row 2 headers: H "Maps⏎(Spreadsheet)", I "Matches⏎(Spreadsheet)". B2 says
  "Region" but column B holds the STAGE, written once per group ("Stage 1" over its rows,
  so it is filled down). The region code in D has no header. Codes: PRE, JP, KR, CN, EMEA, NA,
  PAC, ASIA, CC (Stage 1), EWC (Stage 2 = `Midseason Championship Paris`), WF (Stage 3).
  The last row is `SUM` and is skipped.
- `Hero Index`: role icons in row 2 with the label to the right (B2 → C2 "TANK"); 54 named
  hero pictures. "D.Mon" and "Doctrine" are heroes our catalog does not have yet.
- Map list rows are not evenly spaced: a card that went to a tiebreak after a draw ("Draw!",
  "Map: 3-3") lists a SIXTH map (e.g. Pre-Season W1D1G1, Gibraltar) with no panel left.
- Push maps list distances in the map list (D16 = 70.36, J16 = 109). The panel's "Map: 0-1"
  is the map score.
- Bans can say "Forfeit". Some series list "Map Forfeit" maps (Asia - Stage 1), which the
  Self-Check does not count.
- Some cards have filled map panels before their map list (China/EMEA Stage 3, in
  progress). The Self-Check counts the map list, so the map list defines the maps.
- Winner text has typos ("TOKYO TA1YOs Wins!", "TOKYO TAIYOsWins!"). The ► / ◄ arrow in the
  map list decides then, and the text is listed as unmapped.
- `World Finals Guangzhou` has only its title (C3), no game labels yet: unrecognised.
- One picture (7a2a731516, EMEA - Stage 2) is not in the Hero Index but is used 147 times;
  the inventory lists what the ban text beside it says, for a reviewed `pictures` alias.

## Plan
Steps 2–7 are implemented (`cards.py`, `match_cards` / `summary` / `reference`
purposes, Self-Check comparison, the no-date stage rule, `owcs_cards_fixture.xlsx` +
tests, `inventory --download` / workflow input `inventory`). Verified read-only on the
real sheet from the workflow log. Next: merge, then run a normal sync from main
(it opens the review PR on `auto/sheet-sync`).

### Original plan
1. DONE (uncommitted → committed with these notes): `workbook.read_xlsx` stores each tab's
   picture anchors as `sheet["images"] = [[cell, hash], …]`; `image_anchors()` moved
   into `workbook.py`.
2. New `pipeline/external_sheet/cards.py`: detect blocks by the game-label regex,
   parse with the offsets above (put them in `config/external/gsheet_layout.json` under
   `"matchCards"`, not in code), decode hero pictures via the Hero Index (+ optional
   `aliases.json` `"pictures": {hash: hero}` overrides), bans from text, winner from
   "X Wins!", scores from "Map: a-b", series from G/H. Emit the same External* records
   as normalize.py (maps, bans, appearances with listOrder, comps). Store week/day/game
   and the format label on the match. Skip unplayed blocks (no team names) and count them.
3. `normalize.import_workbook`: add purpose `match_cards` (in DATA_PURPOSES), plus
   `summary`/`reference` purposes for the totals, index and Self-Check tabs
   (by tab-name patterns in config). Region/stage come from the tab name; the year
   comes from Landing Page B1.
4. Validate against Self-Check: per region and stage, compare parsed vs sheet
   matches and maps in the inventory; a mismatch is a warning issue.
5. Reconciliation: the no-date stage rule above.
6. Fixture: `pipeline/fixtures/gsheet/build_cards_fixture.py` (needs PIL locally
   to embed pictures; commit the generated .xlsx). It should cover a Hero Index,
   `EMEA - Stage 2` with a QAD vs Twisted Minds block (Nepal map 1, mirroring the VOD
   map), an unplayed block, Self-Check and a totals tab. Add tests.
7. Workflow: add `inventory --download` (no writes) so the parse can be verified
   from logs; then the user re-runs a normal sync from main.
