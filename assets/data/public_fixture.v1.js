/* =====================================================================
   OWCS Comp Tracker — public_fixture.v1.js  (DEMO DATA)
   Fixture-backed public data contract. Everything here is sample data
   for design/tests. meta.demo=true drives the visible "Demo data"
   ribbon on every public page. Production builds will export a file
   with the same shape (see docs/PUBLIC_DATA_CONTRACT.md) — export_data.py
   never writes to this file.
   Hard rules honored: comps only from cv/manual (never faceit); only
   review_status "reviewed" or "auto-high" may render publicly; manual
   overrides cv; every comp carries an evidence run reference.
   ===================================================================== */
/* Explicit development FALLBACK: production pages load
   public_data.v1.js first; when it has defined OWCS_PUBLIC this
   fixture must NOT overwrite it. */
window.OWCS_PUBLIC = window.OWCS_PUBLIC || {
 "meta": {
  "schema": "public.v1",
  "demo": true,
  "generatedAt": "2026-07-16T21:00:00+00:00",
  "note": "Fixture dataset. Team names reuse the project's existing sample team registry; results, times and comps are invented demo values."
 },

 "regions": [
  {"id": "all",     "name": "All regions",   "short": "ALL"},
  {"id": "na",      "name": "North America", "short": "NA"},
  {"id": "emea",    "name": "EMEA",          "short": "EMEA"},
  {"id": "asia",    "name": "Asia",          "short": "ASIA"},
  {"id": "china",   "name": "China",         "short": "CN"},
  {"id": "pacific", "name": "Pacific",       "short": "PAC"}
 ],

 "teams": [
  {"id": "falcons", "name": "Team Falcons",       "code": "FLC",  "region": "asia", "logoUrl": null},
  {"id": "cr",      "name": "Crazy Raccoon",      "code": "CR",   "region": "asia", "logoUrl": null},
  {"id": "gen",     "name": "Gen.G Esports",      "code": "GEN",  "region": "asia", "logoUrl": null},
  {"id": "zeta",    "name": "ZETA Division",      "code": "ZETA", "region": "asia", "logoUrl": null},
  {"id": "nrg",     "name": "NRG Shock",          "code": "NRG",  "region": "na",   "logoUrl": null},
  {"id": "ssg",     "name": "Spacestation Gaming","code": "SSG",  "region": "na",   "logoUrl": null},
  {"id": "quick",   "name": "Quick Esports",      "code": "QCK",  "region": "emea", "logoUrl": null},
  {"id": "twis",    "name": "Twisted Minds",      "code": "TM",   "region": "emea", "logoUrl": null}
 ],

 "players": [
  {"id": "flc_p1", "teamId": "falcons", "handle": "FLC-TAN1", "role": "Tank"},
  {"id": "flc_p2", "teamId": "falcons", "handle": "FLC-DAM2", "role": "Damage"},
  {"id": "flc_p3", "teamId": "falcons", "handle": "FLC-DAM3", "role": "Damage"},
  {"id": "flc_p4", "teamId": "falcons", "handle": "FLC-SUP4", "role": "Support"},
  {"id": "flc_p5", "teamId": "falcons", "handle": "FLC-SUP5", "role": "Support"},
  {"id": "gen_p1", "teamId": "gen", "handle": "GEN-TAN1", "role": "Tank"},
  {"id": "gen_p2", "teamId": "gen", "handle": "GEN-DAM2", "role": "Damage"},
  {"id": "gen_p3", "teamId": "gen", "handle": "GEN-DAM3", "role": "Damage"},
  {"id": "gen_p4", "teamId": "gen", "handle": "GEN-SUP4", "role": "Support"},
  {"id": "gen_p5", "teamId": "gen", "handle": "GEN-SUP5", "role": "Support"},
  {"id": "cr_p1",  "teamId": "cr", "handle": "CR-TAN1", "role": "Tank"},
  {"id": "cr_p2",  "teamId": "cr", "handle": "CR-DAM2", "role": "Damage"},
  {"id": "cr_p3",  "teamId": "cr", "handle": "CR-DAM3", "role": "Damage"},
  {"id": "cr_p4",  "teamId": "cr", "handle": "CR-SUP4", "role": "Support"},
  {"id": "cr_p5",  "teamId": "cr", "handle": "CR-SUP5", "role": "Support"}
 ],

 "tournaments": [
  {
   "id": "kyoto-inv-2026",
   "name": "OWCS Kyoto Invitational 2026",
   "series": "OWCS",
   "region": "asia",
   "tier": "S",
   "year": 2026,
   "startsAt": "2026-07-10T06:00:00+00:00",
   "endsAt": "2026-07-19T14:00:00+00:00",
   "status": "live",
   "prizePool": "$100,000 USD",
   "teamIds": ["falcons", "cr", "gen", "zeta", "nrg", "ssg", "quick", "twis"],
   "summary": "Eight invited OWCS rosters. Group stage into a double-elimination playoff bracket; the upper-bracket final is on air now.",
   "logoUrl": null,
   "sources": [
    {"type": "faceit",     "url": "https://www.faceit.com/en/ow2/room/1-sample-kyoto", "lastSynced": "2026-07-16T20:40:00+00:00"},
    {"type": "liquipedia", "url": "https://liquipedia.net/overwatch/index.php?search=sample", "lastSynced": "2026-07-15T09:00:00+00:00"}
   ],
   "stages": [
    {"id": "kyoto-groups",   "name": "Group Stage", "order": 1, "format": "round-robin", "status": "completed"},
    {"id": "kyoto-playoffs", "name": "Playoffs",    "order": 2, "format": "double-elim", "status": "live"}
   ],
   "standings": [
    {"stageId": "kyoto-groups", "group": "Group A", "rows": [
     {"teamId": "falcons", "w": 3, "l": 0, "mapDiff": "+7"},
     {"teamId": "nrg",     "w": 2, "l": 1, "mapDiff": "+2"},
     {"teamId": "zeta",    "w": 1, "l": 2, "mapDiff": "-3"},
     {"teamId": "quick",   "w": 0, "l": 3, "mapDiff": "-6"}
    ]},
    {"stageId": "kyoto-groups", "group": "Group B", "rows": [
     {"teamId": "gen",  "w": 3, "l": 0, "mapDiff": "+6"},
     {"teamId": "cr",   "w": 2, "l": 1, "mapDiff": "+3"},
     {"teamId": "ssg",  "w": 1, "l": 2, "mapDiff": "-2"},
     {"teamId": "twis", "w": 0, "l": 3, "mapDiff": "-7"}
    ]}
   ]
  },
  {
   "id": "emea-clash-spring-2026",
   "name": "OWCS EMEA Clash — Spring 2026",
   "series": "OWCS",
   "region": "emea",
   "tier": "A",
   "year": 2026,
   "startsAt": "2026-04-03T16:00:00+00:00",
   "endsAt": "2026-04-05T21:00:00+00:00",
   "status": "completed",
   "prizePool": "$25,000 USD",
   "teamIds": ["twis", "quick"],
   "winnerTeamId": "twis",
   "summary": "Regional final. Twisted Minds took the title 4–2 over Quick Esports.",
   "logoUrl": null,
   "sources": [
    {"type": "manual", "url": null, "lastSynced": "2026-04-06T10:00:00+00:00"}
   ],
   "stages": [
    {"id": "emea-final", "name": "Grand Final", "order": 1, "format": "single-elim", "status": "completed"}
   ],
   "standings": []
  },
  {
   "id": "na-open-q4-2026",
   "name": "OWCS NA Open Qualifier #4",
   "series": "OWCS Open",
   "region": "na",
   "tier": "B",
   "year": 2026,
   "startsAt": "2026-08-08T18:00:00+00:00",
   "endsAt": "2026-08-09T23:00:00+00:00",
   "status": "upcoming",
   "prizePool": null,
   "teamIds": [],
   "summary": "Open registration qualifier. Teams and bracket will appear once registration closes and the seeding is imported.",
   "logoUrl": null,
   "sources": [
    {"type": "faceit", "url": "https://www.faceit.com/en/ow2/room/1-sample-naq4", "lastSynced": "2026-07-14T02:00:00+00:00"}
   ],
   "stages": [],
   "standings": []
  },
  {
   "id": "china-trials-2026",
   "name": "OWCS China Trials 2026",
   "series": "OWCS",
   "region": "china",
   "tier": "A",
   "year": 2026,
   "startsAt": "2026-09-12T08:00:00+00:00",
   "endsAt": "2026-09-20T14:00:00+00:00",
   "status": "upcoming",
   "prizePool": "TBA",
   "teamIds": [],
   "summary": "Trials bracket for the China region. Schedule to be announced.",
   "logoUrl": null,
   "sources": [],
   "stages": [],
   "standings": []
  },
  {
   "id": "pacific-series-2025",
   "name": "OWCS Pacific Series 2025",
   "series": "OWCS",
   "region": "pacific",
   "tier": "A",
   "year": 2025,
   "startsAt": "2025-11-01T04:00:00+00:00",
   "endsAt": "2025-11-09T12:00:00+00:00",
   "status": "completed",
   "prizePool": "$15,000 USD",
   "teamIds": ["zeta", "cr"],
   "winnerTeamId": "zeta",
   "summary": "Last year's Pacific circuit finale, kept for the archive.",
   "logoUrl": null,
   "sources": [
    {"type": "liquipedia", "url": "https://liquipedia.net/overwatch/index.php?search=sample-pacific", "lastSynced": "2025-11-10T08:00:00+00:00"}
   ],
   "stages": [],
   "standings": []
  }
 ],

 /* Bracket structure for kyoto-playoffs.
    feedsWinnerTo / feedsLoserTo reference bracket node ids. */
 "bracketRounds": [
  {"id": "kyoto-ub-sf", "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "side": "upper", "order": 1, "name": "Upper Bracket Semifinals", "bestOf": 5},
  {"id": "kyoto-ub-f",  "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "side": "upper", "order": 2, "name": "Upper Bracket Final",      "bestOf": 5},
  {"id": "kyoto-lb-r1", "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "side": "lower", "order": 1, "name": "Lower Bracket Round 1",   "bestOf": 5},
  {"id": "kyoto-lb-f",  "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "side": "lower", "order": 2, "name": "Lower Bracket Final",     "bestOf": 5},
  {"id": "kyoto-gf",    "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "side": "gf",    "order": 3, "name": "Grand Final",             "bestOf": 7}
 ],
 "bracketMatches": [
  {"id": "bn-ubsf1", "roundId": "kyoto-ub-sf", "position": 1, "matchId": "pm-ubsf1", "feedsWinnerTo": "bn-ubf",  "feedsLoserTo": "bn-lbr1"},
  {"id": "bn-ubsf2", "roundId": "kyoto-ub-sf", "position": 2, "matchId": "pm-ubsf2", "feedsWinnerTo": "bn-ubf",  "feedsLoserTo": "bn-lbr1"},
  {"id": "bn-ubf",   "roundId": "kyoto-ub-f",  "position": 1, "matchId": "pm-ubf",   "feedsWinnerTo": "bn-gf",   "feedsLoserTo": "bn-lbf"},
  {"id": "bn-lbr1",  "roundId": "kyoto-lb-r1", "position": 1, "matchId": "pm-lbr1",  "feedsWinnerTo": "bn-lbf",  "feedsLoserTo": null},
  {"id": "bn-lbf",   "roundId": "kyoto-lb-f",  "position": 1, "matchId": "pm-lbf",   "feedsWinnerTo": "bn-gf",   "feedsLoserTo": null},
  {"id": "bn-gf",    "roundId": "kyoto-gf",    "position": 1, "matchId": "pm-gf",    "feedsWinnerTo": null,      "feedsLoserTo": null},
  {"id": "bn-emea-f","roundId": "emea-f-r1",   "position": 1, "matchId": "pm-emea-final", "feedsWinnerTo": null, "feedsLoserTo": null}
 ],
 /* single round for the EMEA event so its bracket tab renders too */
 "extraRounds": [
  {"id": "emea-f-r1", "tournamentId": "emea-clash-spring-2026", "stageId": "emea-final", "side": "gf", "order": 1, "name": "Grand Final", "bestOf": 7}
 ],

 "matches": [
  {
   "id": "pm-ubsf1",
   "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "roundId": "kyoto-ub-sf",
   "teamA": "falcons", "teamB": "cr", "bestOf": 5,
   "scheduledAt": "2026-07-15T08:00:00+00:00", "status": "completed",
   "scoreA": 3, "scoreB": 1, "winner": "falcons",
   "streamUrl": "https://www.youtube.com/watch?v=AfCXDIMPsLE",
   "faceitUrl": "https://www.faceit.com/en/ow2/room/1-sample-ubsf1",
   "liquipediaUrl": null,
   "casters": ["Sample Caster A", "Sample Caster B"],
   "sources": [{"type": "faceit", "url": "https://www.faceit.com/en/ow2/room/1-sample-ubsf1", "lastSynced": "2026-07-15T12:05:00+00:00"}],
   "captureStatus": "verified",
   "captureRunId": "fix-run-ubsf1",
   "summary": "Falcons dropped the King's Row overtime thriller but closed the series on Colosseo.",
   "maps": [
    {"id": "pm-ubsf1-m1", "order": 1, "map": "busan", "mode": "Control", "winner": "falcons",
     "scoreA": 2, "scoreB": 0,
     "scoreDetail": {"type": "control", "rounds": [{"a": 100, "b": 56}, {"a": 100, "b": 82}]},
     "pickedBy": null, "pickNote": "Opening control map"},
    {"id": "pm-ubsf1-m2", "order": 2, "map": "kingsrow", "mode": "Hybrid", "winner": "falcons",
     "scoreA": 4, "scoreB": 3,
     "scoreDetail": {"type": "hybrid", "a": {"points": 4, "timeBank": "0:00"}, "b": {"points": 3, "timeBank": "1:24"}, "note": "Decided in overtime on the third checkpoint."},
     "pickedBy": "cr", "pickNote": "CR map pick"},
    {"id": "pm-ubsf1-m3", "order": 3, "map": "dorado", "mode": "Escort", "winner": "cr",
     "scoreA": 2, "scoreB": 3,
     "scoreDetail": {"type": "escort", "a": {"points": 2, "timeBank": "0:00"}, "b": {"points": 3, "timeBank": "0:41"}},
     "pickedBy": "falcons", "pickNote": "Falcons map pick"},
    {"id": "pm-ubsf1-m4", "order": 4, "map": "colosseo", "mode": "Push", "winner": "falcons",
     "scoreA": 1, "scoreB": 0,
     "scoreDetail": {"type": "push", "distanceA": "84.2 m", "distanceB": "61.7 m"},
     "pickedBy": "cr", "pickNote": "CR map pick"}
   ]
  },
  {
   "id": "pm-ubsf2",
   "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "roundId": "kyoto-ub-sf",
   "teamA": "gen", "teamB": "nrg", "bestOf": 5,
   "scheduledAt": "2026-07-15T11:00:00+00:00", "status": "completed",
   "scoreA": 3, "scoreB": 2, "winner": "gen",
   "streamUrl": "https://www.youtube.com/watch?v=AfCXDIMPsLE",
   "faceitUrl": "https://www.faceit.com/en/ow2/room/1-sample-ubsf2",
   "liquipediaUrl": null,
   "casters": [],
   "sources": [{"type": "faceit", "url": "https://www.faceit.com/en/ow2/room/1-sample-ubsf2", "lastSynced": "2026-07-15T15:20:00+00:00"}],
   "captureStatus": "needs-review",
   "captureRunId": "fix-run-ubsf2",
   "summary": "Five maps; the Flashpoint decider on Aatlis went the distance.",
   "maps": [
    {"id": "pm-ubsf2-m1", "order": 1, "map": "ilios", "mode": "Control", "winner": "nrg",
     "scoreA": 1, "scoreB": 2,
     "scoreDetail": {"type": "control", "rounds": [{"a": 100, "b": 74}, {"a": 62, "b": 100}, {"a": 91, "b": 100}]},
     "pickedBy": null, "pickNote": "Opening control map"},
    {"id": "pm-ubsf2-m2", "order": 2, "map": "midtown", "mode": "Hybrid", "winner": "gen",
     "scoreA": 3, "scoreB": 2,
     "scoreDetail": {"type": "hybrid", "a": {"points": 3, "timeBank": "0:58"}, "b": {"points": 2, "timeBank": "0:00"}},
     "pickedBy": "nrg", "pickNote": "NRG map pick"},
    {"id": "pm-ubsf2-m3", "order": 3, "map": "circuit", "mode": "Escort", "winner": "nrg",
     "scoreA": 1, "scoreB": 2,
     "scoreDetail": {"type": "escort", "a": {"points": 1, "timeBank": "0:00"}, "b": {"points": 2, "timeBank": "2:03"}},
     "pickedBy": "gen", "pickNote": "Gen.G map pick"},
    {"id": "pm-ubsf2-m4", "order": 4, "map": "esperanca", "mode": "Push", "winner": "gen",
     "scoreA": 1, "scoreB": 0,
     "scoreDetail": {"type": "push", "distanceA": "112.6 m", "distanceB": "97.3 m"},
     "pickedBy": "nrg", "pickNote": "NRG map pick"},
    {"id": "pm-ubsf2-m5", "order": 5, "map": "aatlis", "mode": "Flashpoint", "winner": "gen",
     "scoreA": 3, "scoreB": 2,
     "scoreDetail": {"type": "flashpoint", "capturesA": 3, "capturesB": 2},
     "pickedBy": null, "pickNote": "Decider"}
   ]
  },
  {
   "id": "pm-ubf",
   "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "roundId": "kyoto-ub-f",
   "teamA": "falcons", "teamB": "gen", "bestOf": 5,
   "scheduledAt": "2026-07-17T08:00:00+00:00", "status": "live",
   "scoreA": 1, "scoreB": 1, "winner": null,
   "streamUrl": "https://www.youtube.com/watch?v=AfCXDIMPsLE",
   "faceitUrl": "https://www.faceit.com/en/ow2/room/1-sample-ubf",
   "liquipediaUrl": null,
   "casters": ["Sample Caster A"],
   "sources": [{"type": "faceit", "url": "https://www.faceit.com/en/ow2/room/1-sample-ubf", "lastSynced": "2026-07-17T09:10:00+00:00"}],
   "captureStatus": "capturing",
   "captureRunId": "fix-run-ubf-live",
   "summary": "On air now. Winner goes straight to the Grand Final; the loser drops to the Lower Bracket Final.",
   "maps": [
    {"id": "pm-ubf-m1", "order": 1, "map": "nepal", "mode": "Control", "winner": "falcons",
     "scoreA": 2, "scoreB": 1,
     "scoreDetail": {"type": "control", "rounds": [{"a": 100, "b": 34}, {"a": 55, "b": 100}, {"a": 100, "b": 71}]},
     "pickedBy": null, "pickNote": "Opening control map"},
    {"id": "pm-ubf-m2", "order": 2, "map": "hanaoka", "mode": "Clash", "winner": "gen",
     "scoreA": 3, "scoreB": 5,
     "scoreDetail": {"type": "clash", "pointsA": 3, "pointsB": 5},
     "pickedBy": "gen", "pickNote": "Gen.G map pick"},
    {"id": "pm-ubf-m3", "order": 3, "map": "havana", "mode": "Escort", "winner": null,
     "scoreA": null, "scoreB": null,
     "scoreDetail": null,
     "pickedBy": "falcons", "pickNote": "Falcons map pick", "live": true}
   ]
  },
  {
   "id": "pm-lbr1",
   "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "roundId": "kyoto-lb-r1",
   "teamA": "cr", "teamB": "nrg", "bestOf": 5,
   "scheduledAt": "2026-07-16T08:00:00+00:00", "status": "completed",
   "scoreA": 3, "scoreB": 2, "winner": "cr",
   "streamUrl": "https://www.youtube.com/watch?v=AfCXDIMPsLE",
   "faceitUrl": "https://www.faceit.com/en/ow2/room/1-sample-lbr1",
   "liquipediaUrl": null,
   "casters": [],
   "sources": [{"type": "faceit", "url": "https://www.faceit.com/en/ow2/room/1-sample-lbr1", "lastSynced": "2026-07-16T13:00:00+00:00"}],
   "captureStatus": "queued",
   "captureRunId": null,
   "summary": "Crazy Raccoon survive elimination in five.",
   "maps": [
    {"id": "pm-lbr1-m1", "order": 1, "map": "oasis", "mode": "Control", "winner": "cr",
     "scoreA": 2, "scoreB": 1,
     "scoreDetail": {"type": "control", "rounds": [{"a": 100, "b": 88}, {"a": 47, "b": 100}, {"a": 100, "b": 62}]},
     "pickedBy": null, "pickNote": "Opening control map"},
    {"id": "pm-lbr1-m2", "order": 2, "map": "paraiso", "mode": "Hybrid", "winner": "nrg",
     "scoreA": 2, "scoreB": 3,
     "scoreDetail": {"type": "hybrid", "a": {"points": 2, "timeBank": "0:00"}, "b": {"points": 3, "timeBank": "0:12"}},
     "pickedBy": "nrg", "pickNote": "NRG map pick"},
    {"id": "pm-lbr1-m3", "order": 3, "map": "junkertown", "mode": "Escort", "winner": "cr",
     "scoreA": 3, "scoreB": 2,
     "scoreDetail": {"type": "escort", "a": {"points": 3, "timeBank": "1:07"}, "b": {"points": 2, "timeBank": "0:00"}},
     "pickedBy": "cr", "pickNote": "CR map pick"},
    {"id": "pm-lbr1-m4", "order": 4, "map": "runasapi", "mode": "Push", "winner": "nrg",
     "scoreA": 0, "scoreB": 1,
     "scoreDetail": {"type": "push", "distanceA": "45.0 m", "distanceB": "58.9 m"},
     "pickedBy": "nrg", "pickNote": "NRG map pick"},
    {"id": "pm-lbr1-m5", "order": 5, "map": "suravasa", "mode": "Flashpoint", "winner": "cr",
     "scoreA": 3, "scoreB": 1,
     "scoreDetail": {"type": "flashpoint", "capturesA": 3, "capturesB": 1},
     "pickedBy": null, "pickNote": "Decider"}
   ]
  },
  {
   "id": "pm-lbf",
   "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "roundId": "kyoto-lb-f",
   "teamA": "cr", "teamB": null, "bestOf": 5,
   "scheduledAt": "2026-07-18T08:00:00+00:00", "status": "upcoming",
   "scoreA": null, "scoreB": null, "winner": null,
   "streamUrl": "https://www.youtube.com/watch?v=AfCXDIMPsLE",
   "faceitUrl": null, "liquipediaUrl": null,
   "casters": [],
   "sources": [],
   "captureStatus": "needs-source",
   "captureRunId": null,
   "summary": "Crazy Raccoon await the loser of the Upper Bracket Final.",
   "tbdNote": "Loser of Upper Bracket Final",
   "maps": []
  },
  {
   "id": "pm-gf",
   "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-playoffs", "roundId": "kyoto-gf",
   "teamA": null, "teamB": null, "bestOf": 7,
   "scheduledAt": "2026-07-19T08:00:00+00:00", "status": "upcoming",
   "scoreA": null, "scoreB": null, "winner": null,
   "streamUrl": "https://www.youtube.com/watch?v=AfCXDIMPsLE",
   "faceitUrl": null, "liquipediaUrl": null,
   "casters": [],
   "sources": [],
   "captureStatus": "needs-source",
   "captureRunId": null,
   "summary": "Best of seven for the title.",
   "tbdNote": "Winners of Upper and Lower Bracket Finals",
   "maps": []
  },
  {
   "id": "pm-groups-forfeit",
   "tournamentId": "kyoto-inv-2026", "stageId": "kyoto-groups", "roundId": null,
   "teamA": "zeta", "teamB": "twis", "bestOf": 3,
   "scheduledAt": "2026-07-11T09:00:00+00:00", "status": "forfeit",
   "scoreA": 1, "scoreB": 0, "winner": "zeta",
   "streamUrl": null, "faceitUrl": "https://www.faceit.com/en/ow2/room/1-sample-grp-ff", "liquipediaUrl": null,
   "casters": [],
   "sources": [{"type": "faceit", "url": "https://www.faceit.com/en/ow2/room/1-sample-grp-ff", "lastSynced": "2026-07-11T10:00:00+00:00"}],
   "captureStatus": "failed",
   "captureRunId": "fix-run-ff-failed",
   "summary": "Twisted Minds forfeited the group-stage match; awarded 1–0 to ZETA Division.",
   "maps": []
  },
  {
   "id": "pm-emea-final",
   "tournamentId": "emea-clash-spring-2026", "stageId": "emea-final", "roundId": "emea-f-r1",
   "teamA": "twis", "teamB": "quick", "bestOf": 7,
   "scheduledAt": "2026-04-05T17:00:00+00:00", "status": "completed",
   "scoreA": 4, "scoreB": 2, "winner": "twis",
   "streamUrl": null,
   "faceitUrl": null,
   "liquipediaUrl": "https://liquipedia.net/overwatch/index.php?search=sample-emea",
   "casters": [],
   "sources": [{"type": "manual", "url": null, "lastSynced": "2026-04-06T10:00:00+00:00"}],
   "captureStatus": "needs-source",
   "captureRunId": null,
   "summary": "Series recorded from the official scoreline. No VOD source has been linked yet, so per-map detail and comps are unavailable.",
   "maps": []
  }
 ],

 /* Hero bans — match facts (FACEIT / manual). Bans are facts, never comps. */
 "heroBans": [
  {"id": "ban-1", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m1", "teamId": "falcons", "hero": "tracer", "order": 1, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-2", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m1", "teamId": "cr",      "hero": "juno",   "order": 2, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-3", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m2", "teamId": "falcons", "hero": "cass",   "order": 1, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-4", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m2", "teamId": "cr",      "hero": "kiriko", "order": 2, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-5", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m3", "teamId": "falcons", "hero": "mei",    "order": 1, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-6", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m3", "teamId": "cr",      "hero": "winston","order": 2, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-7", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m4", "teamId": "falcons", "hero": "juno",   "order": 1, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-8", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m4", "teamId": "cr",      "hero": "dva",    "order": 2, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-9",  "matchId": "pm-ubsf2", "mapId": "pm-ubsf2-m1", "teamId": "gen", "hero": "sombra", "order": 1, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-10", "matchId": "pm-ubsf2", "mapId": "pm-ubsf2-m1", "teamId": "nrg", "hero": "ana",    "order": 2, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-11", "matchId": "pm-lbr1",  "mapId": "pm-lbr1-m1",  "teamId": "cr",  "hero": "tracer", "order": 1, "phase": "pre-map", "source": "faceit"},
  {"id": "ban-12", "matchId": "pm-lbr1",  "mapId": "pm-lbr1-m1",  "teamId": "nrg", "hero": "kiriko", "order": 2, "phase": "pre-map", "source": "faceit"}
 ],

 /* Hero swaps — temporal-consensus verdicts (demo). Confirmed rows carry
    before/after evidence crops that resolve to real fixture assets;
    rejected rows carry the honest reason they were thrown out. */
 "heroSwaps": [
  {"id": "sw-pm-ubsf1-m1-cr-3-870-confirmed", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m1",
   "teamId": "cr", "side": "b", "slot": 3, "fromHero": "sombra", "toHero": "tracer",
   "offset": 870, "confidence": 0.84, "status": "confirmed",
   "reason": "tracer persisted 3 obs while sombra no longer detected",
   "evidenceBefore": "reports/capture_trial/crops/005400_B1.png",
   "evidenceAfter": "reports/capture_trial/crops/005400_A1.png",
   "ingestId": "fix-run-ubsf1"},
  {"id": "sw-pm-ubsf1-m1-falcons-2-905-rejected", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m1",
   "teamId": "falcons", "side": "a", "slot": 2, "fromHero": "tracer", "toHero": "sojourn",
   "offset": 905, "confidence": null, "status": "rejected",
   "reason": "candidate sojourn seen 1x then tracer returned — noise, not a swap",
   "evidenceBefore": null, "evidenceAfter": null, "ingestId": "fix-run-ubsf1"}
 ],

 /* Capture runs — the pipeline evidence spine. Resolutions are honest:
    requested vs actual is always shown. Report/frame paths point at real
    fixture assets that ship in this repo (reports/capture_trial/...). */
 "captureRuns": [
  {
   "id": "fix-run-ubsf1", "matchId": "pm-ubsf1", "sourceId": "owcs-afcxdimpsle",
   "window": {"start": "1:30:00", "end": "1:32:00", "every": 30},
   "requestedHeight": 1080, "actualWidth": 1920, "actualHeight": 1080,
   "clipMode": "local-window", "status": "verified",
   "reportPath": "reports/capture_trial/index.html",
   "createdAt": "2026-07-15T13:30:00+00:00",
   "frames": [
    {"offset": 5400, "file": "reports/capture_trial/frames/005400.png", "layoutDebug": "reports/capture_trial/layout_debug/005400_debug.png"},
    {"offset": 5430, "file": "reports/capture_trial/frames/005430.png", "layoutDebug": "reports/capture_trial/layout_debug/005430_debug.png"}
   ],
   "crops": [
    "reports/capture_trial/crops/005400_A1.png",
    "reports/capture_trial/crops/005400_A2.png",
    "reports/capture_trial/crops/005400_A3.png",
    "reports/capture_trial/crops/005400_A4.png",
    "reports/capture_trial/crops/005400_A5.png",
    "reports/capture_trial/crops/005400_B1.png"
   ]
  },
  {
   "id": "fix-run-ubsf2", "matchId": "pm-ubsf2", "sourceId": "owcs-afcxdimpsle",
   "window": {"start": "4:10:00", "end": "4:12:00", "every": 30},
   "requestedHeight": 1080, "actualWidth": 1280, "actualHeight": 720,
   "clipMode": "local-window", "status": "needs-review",
   "reportPath": "reports/capture_trial/index.html",
   "createdAt": "2026-07-15T16:00:00+00:00",
   "frames": [
    {"offset": 15000, "file": "reports/capture_trial/frames/005430.png", "layoutDebug": "reports/capture_trial/layout_debug/005430_debug.png"}
   ],
   "crops": [],
   "note": "Source VOD capped at 720p — flagged for review before any detection is trusted."
  },
  {
   "id": "fix-run-ubf-live", "matchId": "pm-ubf", "sourceId": "owcs-afcxdimpsle",
   "window": {"start": "0:05:00", "end": null, "every": 60},
   "requestedHeight": 1080, "actualWidth": null, "actualHeight": null,
   "clipMode": "local-window", "status": "capturing",
   "reportPath": null,
   "createdAt": "2026-07-17T08:10:00+00:00",
   "frames": [], "crops": []
  },
  {
   "id": "fix-run-ff-failed", "matchId": "pm-groups-forfeit", "sourceId": null,
   "window": null,
   "requestedHeight": 1080, "actualWidth": null, "actualHeight": null,
   "clipMode": null, "status": "failed",
   "reportPath": null,
   "createdAt": "2026-07-11T10:05:00+00:00",
   "frames": [], "crops": [],
   "note": "Match was forfeited before broadcast — no VOD exists to capture."
  }
 ],

 /* Comp snapshots — the moat. source is ONLY "cv" or "manual"; FACEIT can
    never appear here. Public pages may render ONLY review_status
    "reviewed" or "auto-high". The needs-review row below exists to prove
    the public filter works (it must never render on a fan page).
    Manual snapshots override cv snapshots that share overridesId. */
 "compSnapshots": [
  {
   "id": "cs-ubsf1-busan-a-600", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m1",
   "teamId": "falcons", "side": "a", "timestamp": 600,
   "heroes": ["winston", "tracer", "genji", "kiriko", "juno"],
   "source": "cv", "confidence": 0.97, "reviewStatus": "auto-high",
   "evidenceRunId": "fix-run-ubsf1",
   "evidenceFrame": "reports/capture_trial/frames/005400.png"
  },
  {
   "id": "cs-ubsf1-busan-b-600-cv", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m1",
   "teamId": "cr", "side": "b", "timestamp": 600,
   "heroes": ["winston", "sojourn", "sombra", "kiriko", "juno"],
   "source": "cv", "confidence": 0.81, "reviewStatus": "reviewed",
   "overriddenBy": "cs-ubsf1-busan-b-600-manual",
   "evidenceRunId": "fix-run-ubsf1",
   "evidenceFrame": "reports/capture_trial/frames/005400.png"
  },
  {
   "id": "cs-ubsf1-busan-b-600-manual", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m1",
   "teamId": "cr", "side": "b", "timestamp": 600,
   "heroes": ["winston", "sojourn", "tracer", "kiriko", "juno"],
   "source": "manual", "confidence": 1.0, "reviewStatus": "reviewed",
   "overridesId": "cs-ubsf1-busan-b-600-cv",
   "correction": {"note": "Slot B3 misread Sombra; corrected to Tracer from frame 005400.", "author": "reviewer", "appliedAt": "2026-07-15T18:20:00+00:00"},
   "evidenceRunId": "fix-run-ubsf1",
   "evidenceFrame": "reports/capture_trial/frames/005400.png"
  },
  {
   "id": "cs-ubsf1-kr-a-1200", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m2",
   "teamId": "falcons", "side": "a", "timestamp": 1200,
   "heroes": ["jq", "mei", "sojourn", "ana", "juno"],
   "source": "cv", "confidence": 0.96, "reviewStatus": "auto-high",
   "evidenceRunId": "fix-run-ubsf1",
   "evidenceFrame": "reports/capture_trial/frames/005430.png"
  },
  {
   "id": "cs-ubsf1-kr-b-1200", "matchId": "pm-ubsf1", "mapId": "pm-ubsf1-m2",
   "teamId": "cr", "side": "b", "timestamp": 1200,
   "heroes": ["rein", "reaper", "mei", "ana", "lucio"],
   "source": "cv", "confidence": 0.95, "reviewStatus": "auto-high",
   "evidenceRunId": "fix-run-ubsf1",
   "evidenceFrame": "reports/capture_trial/frames/005430.png"
  },
  {
   "id": "cs-ubsf2-ilios-a-540", "matchId": "pm-ubsf2", "mapId": "pm-ubsf2-m1",
   "teamId": "gen", "side": "a", "timestamp": 540,
   "heroes": ["dva", "genji", "hanzo", "bap", "lucio"],
   "source": "cv", "confidence": 0.62, "reviewStatus": "needs-review",
   "evidenceRunId": "fix-run-ubsf2",
   "evidenceFrame": "reports/capture_trial/frames/005430.png",
   "note": "720p capture — below the confidence floor; must not render publicly until reviewed."
  }
 ],

 "vodSources": [
  {"id": "owcs-afcxdimpsle", "provider": "youtube", "url": "https://www.youtube.com/watch?v=AfCXDIMPsLE", "title": "OWCS sample broadcast VOD", "matchIds": ["pm-ubsf1", "pm-ubsf2", "pm-ubf", "pm-lbr1"], "heightAvailable": 1080}
 ],

 "heroes": [
  {"id": "ana", "name": "Ana", "role": "Support"},
  {"id": "ashe", "name": "Ashe", "role": "Damage"},
  {"id": "ball", "name": "Wrecking Ball", "role": "Tank"},
  {"id": "bap", "name": "Baptiste", "role": "Support"},
  {"id": "cass", "name": "Cassidy", "role": "Damage"},
  {"id": "dva", "name": "D.Va", "role": "Tank"},
  {"id": "echo", "name": "Echo", "role": "Damage"},
  {"id": "freja", "name": "Freja", "role": "Damage"},
  {"id": "genji", "name": "Genji", "role": "Damage"},
  {"id": "hanzo", "name": "Hanzo", "role": "Damage"},
  {"id": "jq", "name": "Junker Queen", "role": "Tank"},
  {"id": "juno", "name": "Juno", "role": "Support"},
  {"id": "kiriko", "name": "Kiriko", "role": "Support"},
  {"id": "lucio", "name": "Lúcio", "role": "Support"},
  {"id": "mei", "name": "Mei", "role": "Damage"},
  {"id": "reaper", "name": "Reaper", "role": "Damage"},
  {"id": "rein", "name": "Reinhardt", "role": "Tank"},
  {"id": "sigma", "name": "Sigma", "role": "Tank"},
  {"id": "sojourn", "name": "Sojourn", "role": "Damage"},
  {"id": "sombra", "name": "Sombra", "role": "Damage"},
  {"id": "tracer", "name": "Tracer", "role": "Damage"},
  {"id": "widow", "name": "Widowmaker", "role": "Damage"},
  {"id": "winston", "name": "Winston", "role": "Tank"}
 ],

 "mapsCatalog": [
  {"id": "aatlis", "name": "Aatlis", "mode": "Flashpoint"},
  {"id": "busan", "name": "Busan", "mode": "Control"},
  {"id": "circuit", "name": "Circuit Royal", "mode": "Escort"},
  {"id": "colosseo", "name": "Colosseo", "mode": "Push"},
  {"id": "dorado", "name": "Dorado", "mode": "Escort"},
  {"id": "esperanca", "name": "Esperança", "mode": "Push"},
  {"id": "hanaoka", "name": "Hanaoka", "mode": "Clash"},
  {"id": "havana", "name": "Havana", "mode": "Escort"},
  {"id": "ilios", "name": "Ilios", "mode": "Control"},
  {"id": "junkertown", "name": "Junkertown", "mode": "Escort"},
  {"id": "kingsrow", "name": "King's Row", "mode": "Hybrid"},
  {"id": "midtown", "name": "Midtown", "mode": "Hybrid"},
  {"id": "nepal", "name": "Nepal", "mode": "Control"},
  {"id": "oasis", "name": "Oasis", "mode": "Control"},
  {"id": "paraiso", "name": "Paraíso", "mode": "Hybrid"},
  {"id": "runasapi", "name": "Runasapi", "mode": "Push"},
  {"id": "suravasa", "name": "Suravasa", "mode": "Flashpoint"}
 ],

 "patches": [
  {"id": "s9-mid", "name": "Season 9 Midseason", "from": "2026-06-17"},
  {"id": "s9-launch", "name": "Season 9 Launch", "from": "2026-05-05"}
 ]
};
