-- =====================================================================
-- OWCS Comp Tracker — SQLite schema
-- One file DB (data/owcs.sqlite). Static frontend reads exported data.js.
-- Milestone 1 adds FACEIT matchroom metadata, replay codes, hero bans,
-- map scores, and analyst prep notes while keeping the vision tables simple.
-- =====================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Reference tables (seeded once) -------------------------------------
CREATE TABLE IF NOT EXISTS heroes (
  id   TEXT PRIMARY KEY,          -- e.g. 'kiriko'
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('Tank','Damage','Support'))
);

CREATE TABLE IF NOT EXISTS game_maps (
  id   TEXT PRIMARY KEY,          -- e.g. 'kingsrow'
  name TEXT NOT NULL,
  mode TEXT NOT NULL              -- Control / Escort / Hybrid / Push / Flashpoint / Clash
);

CREATE TABLE IF NOT EXISTS teams (
  id             TEXT PRIMARY KEY, -- e.g. 'falcons'
  name           TEXT NOT NULL,
  region         TEXT NOT NULL DEFAULT 'Unknown',
  code           TEXT NOT NULL,    -- short tag, e.g. 'FLC'
  faceit_team_id TEXT UNIQUE,
  logo_url       TEXT,
  prep_notes     TEXT
);

-- Core match metadata (FACEIT/results ingest + vision fill this) -------
CREATE TABLE IF NOT EXISTS matches (
  id              TEXT PRIMARY KEY, -- internal id, e.g. 'm01' or source slug
  source_ref      TEXT UNIQUE,      -- source id/slug for idempotent upserts
  faceit_match_id TEXT UNIQUE,
  faceit_room_url TEXT,
  event_name      TEXT,
  season          TEXT,
  stage           TEXT,
  division        TEXT,
  round           TEXT,
  group_name      TEXT,
  region          TEXT NOT NULL DEFAULT 'Unknown',
  date            TEXT NOT NULL,    -- ISO 'YYYY-MM-DD' for static sorting
  scheduled_at    TEXT,
  started_at      TEXT,
  finished_at     TEXT,
  status          TEXT NOT NULL DEFAULT 'final'
                  CHECK (status IN ('upcoming','live','final','unknown')),
  -- Phase B discovery adds a precise FACEIT lifecycle word and a coarse
  -- capture state alongside the CHECK-constrained `status` above.
  lifecycle_status TEXT,             -- scheduled/live/finished/cancelled/forfeit/aborted
  capture_status   TEXT,             -- pending / cancelled / ... (discovery-side)
  competition_id   TEXT,             -- FACEIT competition/registry id
  team_a          TEXT NOT NULL REFERENCES teams(id),
  team_b          TEXT NOT NULL REFERENCES teams(id),
  score_a         INTEGER DEFAULT 0,
  score_b         INTEGER DEFAULT 0,
  winner_team     TEXT REFERENCES teams(id),
  source_url      TEXT,
  vod_url         TEXT,             -- set when the broadcast VOD is known
  raw_source      TEXT,
  prep_notes      TEXT,
  updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS map_results (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id            TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  map_order           INTEGER NOT NULL,   -- 1..n in broadcast order
  map_id              TEXT NOT NULL REFERENCES game_maps(id),
  score_a             INTEGER,
  score_b             INTEGER,
  winner_team         TEXT REFERENCES teams(id),
  picked_by_team      TEXT REFERENCES teams(id),
  veto_action         TEXT,               -- pick / ban / decider / unknown
  pick_veto           TEXT,               -- readable source text when available
  replay_code         TEXT,
  replay_expires_note TEXT,
  vod_url             TEXT,
  vod_start_seconds   INTEGER,
  source              TEXT DEFAULT 'manual',
  confidence          REAL,
  notes               TEXT,
  UNIQUE (match_id, map_order)
);

-- FACEIT/OWCS hero bans, usually per map. team_id can be NULL if the
-- matchroom only exposes the ban without ownership. source='cv' rows come
-- from pipeline/detect_bans.py (OCR'd off the broadcast pick/ban screen,
-- generalized across overlay layouts, never guessed silently); ingest_id +
-- evidence_path give them the same click-through proof as CV comps.
CREATE TABLE IF NOT EXISTS hero_bans (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id       TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  map_result_id  INTEGER REFERENCES map_results(id) ON DELETE SET NULL,
  map_order      INTEGER,
  team_id        TEXT REFERENCES teams(id),
  hero_id        TEXT NOT NULL REFERENCES heroes(id),
  role           TEXT,
  ban_order      INTEGER,
  source         TEXT DEFAULT 'faceit',    -- faceit | manual_facts | sample | cv
  confidence     REAL,
  notes          TEXT,
  ingest_id      TEXT REFERENCES ingest_runs(id),
  evidence_path  TEXT                      -- crop backing a source='cv' row
);

-- Optional structured map pick/veto timeline. This supports FACEIT when it
-- exposes a full veto flow; otherwise map_results.pick_veto is enough.
CREATE TABLE IF NOT EXISTS map_veto_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id    TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  order_index INTEGER NOT NULL,
  team_id     TEXT REFERENCES teams(id),
  map_id      TEXT REFERENCES game_maps(id),
  action      TEXT NOT NULL DEFAULT 'unknown'
              CHECK (action IN ('pick','ban','decider','unknown')),
  source      TEXT DEFAULT 'faceit',
  notes       TEXT,
  UNIQUE (match_id, order_index)
);

-- Human/analyst notes can sit beside automatic data without blocking export.
CREATE TABLE IF NOT EXISTS team_prep_notes (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id          TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  opponent_team_id TEXT REFERENCES teams(id) ON DELETE SET NULL,
  map_id           TEXT REFERENCES game_maps(id) ON DELETE SET NULL,
  note_type        TEXT DEFAULT 'general', -- map_pool / ban / comp / replay / general
  note             TEXT NOT NULL,
  source           TEXT DEFAULT 'manual',
  updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Raw FACEIT responses are cached for reproducibility and future parser work.
CREATE TABLE IF NOT EXISTS faceit_raw_cache (
  cache_key   TEXT PRIMARY KEY,
  url         TEXT,
  fetched_at  TEXT NOT NULL,
  status_code INTEGER,
  body_path   TEXT,
  sha256      TEXT,
  error       TEXT
);

-- One row per accepted frame per team. map_result_id is NULL until the
-- map-sync stage assigns it.
CREATE TABLE IF NOT EXISTS comp_snapshots (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id              TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  map_result_id         INTEGER REFERENCES map_results(id) ON DELETE SET NULL,
  team_id               TEXT NOT NULL REFERENCES teams(id),
  stream_offset_seconds INTEGER NOT NULL,
  overall_confidence    REAL,
  frame_hash            TEXT,     -- dedup across reruns
  source                TEXT DEFAULT 'cv',  -- tracker provenance: cv|replay|manual
  UNIQUE (frame_hash, team_id)
);

CREATE TABLE IF NOT EXISTS snapshot_heroes (
  snapshot_id INTEGER NOT NULL REFERENCES comp_snapshots(id) ON DELETE CASCADE,
  slot        INTEGER NOT NULL,   -- 1..5 on the HUD
  hero_id     TEXT NOT NULL REFERENCES heroes(id),
  confidence  REAL,
  PRIMARY KEY (snapshot_id, slot)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_match ON comp_snapshots(match_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_mapresult ON comp_snapshots(map_result_id);
CREATE INDEX IF NOT EXISTS idx_mapresults_match ON map_results(match_id);
CREATE INDEX IF NOT EXISTS idx_hero_bans_match ON hero_bans(match_id);
CREATE INDEX IF NOT EXISTS idx_veto_match ON map_veto_events(match_id);
CREATE INDEX IF NOT EXISTS idx_prep_team ON team_prep_notes(team_id);

-- Full-map ingestion (staged, auditable CV writes) ------------------------
-- Stage 1: every sampled frame/slot produces a raw observation (accepted or
-- not) so the whole timeline can be reconstructed and audited.
-- Stage 2: temporal consensus turns observations into hero_stints + swaps.
-- Stage 3: review/promotion state gates what the public export may see.

CREATE TABLE IF NOT EXISTS ingest_runs (
  id                  TEXT PRIMARY KEY,   -- e.g. 'nepal-qad-tm_1843_v1'
  source_id           TEXT,               -- video_sources id / broadcast slug
  vod_url             TEXT,
  match_id            TEXT REFERENCES matches(id),
  map_order           INTEGER,
  start_offset        INTEGER,            -- stream offset seconds (map start)
  end_offset          INTEGER,
  detector_version    TEXT NOT NULL,
  calibration_profile TEXT,               -- layouts/*.json used
  calibration_version TEXT,
  status              TEXT NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running','complete','failed')),
  stats_json          TEXT,               -- sampling/coverage counters
  -- calibration_health is a RUNTIME measurement (this ingest's own accepted
  -- observations), distinct from calibrate_source.py's one-time offline
  -- confidence: a calibration can score well in isolation and still drift
  -- on a different capture. See ingest_map.calibration_health().
  calibration_health  TEXT,               -- json: {status, reasons[], metrics{}}
  calibration_status  TEXT DEFAULT 'ok'
                      CHECK (calibration_status IN ('ok','suspect')),
  report_path         TEXT,
  created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Staged, reviewable CV findings that are NOT comps/bans/rounds: team-name
-- OCR candidates, event/stage/date-bug OCR candidates, and anything else
-- that needs a human's eyes before it overrides operator-supplied facts.
-- Mirrors the hero_stints/hero_swaps "never silently promote" pattern.
CREATE TABLE IF NOT EXISTS ingest_findings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ingest_id   TEXT NOT NULL REFERENCES ingest_runs(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK (kind IN
              ('team_identity','ban_candidate','event_metadata',
               'calibration_health')),
  field       TEXT,                       -- e.g. 'a' / 'b' side, or 'event_name'
  raw_text    TEXT,                       -- what OCR actually read
  value       TEXT,                       -- resolved value (team_id, hero_id, ...)
  confidence  REAL,
  method      TEXT,                       -- exact | fuzzy | ambiguous | ...
  evidence_path TEXT,
  status      TEXT NOT NULL DEFAULT 'candidate'
              CHECK (status IN ('candidate','confirmed','rejected')),
  notes       TEXT,
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_findings_ingest ON ingest_findings(ingest_id);

CREATE TABLE IF NOT EXISTS slot_observations (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  ingest_id      TEXT NOT NULL REFERENCES ingest_runs(id) ON DELETE CASCADE,
  offset_seconds REAL NOT NULL,           -- stream offset of the frame
  side           TEXT NOT NULL CHECK (side IN ('a','b')),
  slot           INTEGER NOT NULL,        -- 1..5 on the HUD
  team_id        TEXT REFERENCES teams(id),
  state          TEXT NOT NULL,           -- gameplay | no-hud | replay | ...
  hero_top       TEXT,                    -- best candidate ('' = none)
  score_top      REAL,
  hero_second    TEXT,                    -- runner-up candidate
  score_second   REAL,
  margin         REAL,                    -- score_top - score_second
  accepted       INTEGER NOT NULL DEFAULT 0,
  reject_reason  TEXT,                    -- why not accepted (if not)
  frame_path     TEXT,                    -- evidence: full frame
  crop_path      TEXT,                    -- evidence: slot crop
  template_used  TEXT,                    -- matching template filename
  UNIQUE (ingest_id, offset_seconds, side, slot)
);

CREATE TABLE IF NOT EXISTS map_rounds (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  map_result_id INTEGER NOT NULL REFERENCES map_results(id) ON DELETE CASCADE,
  round_index   INTEGER NOT NULL,         -- 1..n
  start_offset  INTEGER,
  end_offset    INTEGER,
  confidence    REAL,
  source        TEXT DEFAULT 'cv',
  notes         TEXT,
  UNIQUE (map_result_id, round_index)
);

CREATE TABLE IF NOT EXISTS hero_stints (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  ingest_id        TEXT REFERENCES ingest_runs(id),
  match_id         TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  map_result_id    INTEGER REFERENCES map_results(id) ON DELETE SET NULL,
  team_id          TEXT NOT NULL REFERENCES teams(id),
  side             TEXT CHECK (side IN ('a','b')),
  slot             INTEGER NOT NULL,
  hero_id          TEXT NOT NULL REFERENCES heroes(id),
  start_offset     INTEGER NOT NULL,      -- stream offset seconds
  end_offset       INTEGER,
  n_obs            INTEGER,               -- accepted observations inside
  mean_conf        REAL,
  min_conf         REAL,
  status           TEXT NOT NULL DEFAULT 'needs-review'
                   CHECK (status IN ('auto-high','needs-review','reviewed',
                                     'rejected')),
  source           TEXT NOT NULL DEFAULT 'cv',  -- cv | manual
  detector_version TEXT,
  evidence_start   TEXT,                  -- crop path near start
  evidence_end     TEXT,
  manual_override  INTEGER NOT NULL DEFAULT 0,  -- 1 = human-corrected, keep
  notes            TEXT,
  created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at       TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (match_id, map_result_id, team_id, slot, start_offset,
          detector_version)
);

CREATE TABLE IF NOT EXISTS hero_swaps (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  ingest_id        TEXT REFERENCES ingest_runs(id),
  match_id         TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  map_result_id    INTEGER REFERENCES map_results(id) ON DELETE SET NULL,
  team_id          TEXT NOT NULL REFERENCES teams(id),
  side             TEXT CHECK (side IN ('a','b')),
  slot             INTEGER NOT NULL,
  from_hero        TEXT REFERENCES heroes(id),
  to_hero          TEXT NOT NULL REFERENCES heroes(id),
  offset_seconds   INTEGER NOT NULL,      -- earliest defensible swap time
  confidence       REAL,
  status           TEXT NOT NULL DEFAULT 'uncertain'
                   CHECK (status IN ('confirmed','rejected','uncertain')),
  reason           TEXT,                  -- acceptance/rejection explanation
  evidence_before  TEXT,
  evidence_after   TEXT,
  detector_version TEXT,
  manual_override  INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (match_id, map_result_id, team_id, slot, offset_seconds, to_hero,
          detector_version)
);

CREATE INDEX IF NOT EXISTS idx_obs_ingest ON slot_observations(ingest_id);
CREATE INDEX IF NOT EXISTS idx_stints_map ON hero_stints(map_result_id);
CREATE INDEX IF NOT EXISTS idx_swaps_map ON hero_swaps(map_result_id);

-- FACEIT-sourced rosters --------------------------------------------------
-- Players and per-match lineups come from FACEIT matchroom data. These are
-- source-of-truth FACEIT fields, never tracker-generated.
CREATE TABLE IF NOT EXISTS players (
  id               TEXT PRIMARY KEY,       -- internal slug, e.g. 'proper'
  nickname         TEXT NOT NULL,
  faceit_player_id TEXT UNIQUE,
  team_id          TEXT REFERENCES teams(id) ON DELETE SET NULL,
  role             TEXT,                   -- Tank / Damage / Support if known
  country          TEXT,
  source           TEXT DEFAULT 'faceit'
);

CREATE TABLE IF NOT EXISTS match_rosters (
  match_id  TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  team_id   TEXT NOT NULL REFERENCES teams(id),
  player_id TEXT NOT NULL REFERENCES players(id),
  source    TEXT DEFAULT 'faceit',
  PRIMARY KEY (match_id, team_id, player_id)
);
