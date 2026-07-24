-- =====================================================================
-- OWCS Comp Tracker — automation job database (Roadmap Phase A1)
-- ---------------------------------------------------------------------
-- A PERSISTENT job/state store, separate from the content DB
-- (data/owcs.sqlite). The roadmap is explicit: "Do not use workflow
-- artifacts as the primary job queue." This is that queue.
--
-- Design rules baked into the schema:
--   * No record disappears when something fails. Every job keeps its
--     error code, message, attempt count, timestamps, worker identity,
--     source URL and diagnostic path (see "Automation state machine").
--   * Every job has a DETERMINISTIC identity (job_key) so running the
--     system twice never duplicates work (Phase A2 idempotency).
--   * State transitions are validated in code (state_machine.py); the
--     `state` column just stores the current node.
-- =====================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- --- Discovery inputs (registries mirrored into the DB for joins) -----
CREATE TABLE IF NOT EXISTS source_channels (
  id                TEXT PRIMARY KEY,       -- e.g. 'ow_esports_global'
  name              TEXT,
  platform          TEXT,                   -- youtube / bilibili / ...
  channel_id        TEXT,                   -- platform channel id (nullable until confirmed)
  region            TEXT,
  language          TEXT,
  official          INTEGER NOT NULL DEFAULT 0,
  priority          INTEGER NOT NULL DEFAULT 0,
  enabled           INTEGER NOT NULL DEFAULT 0,
  source_url        TEXT,                   -- candidate/confirmed official channel URL (C1)
  ownership_evidence TEXT,                  -- why this channel is believed official (C1)
  verified_date     TEXT,                   -- last time channel_id was API-verified
  verified_status   TEXT NOT NULL DEFAULT 'unverified', -- verified/unverified/stale/failed
  disabled_reason   TEXT,                   -- why an entry stays disabled (never guessed)
  preferred_layout  TEXT,                   -- optional calibrated-layout id (Phase E4)
  updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_events (
  id                TEXT PRIMARY KEY,       -- competition/event key
  source            TEXT NOT NULL,          -- 'faceit' / 'owcs_calendar' / ...
  external_id       TEXT,                   -- championship/tournament id
  name              TEXT,
  region            TEXT,
  tier              INTEGER,
  state             TEXT NOT NULL DEFAULT 'DISCOVERED',
  raw              TEXT,                     -- JSON blob of the source payload
  first_seen_at     TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at        TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (source, external_id)
);

-- --- Scheduled matches (FACEIT-authoritative facts, Phase B) ----------
CREATE TABLE IF NOT EXISTS scheduled_matches (
  id                TEXT PRIMARY KEY,       -- match:<faceit-match-id>
  faceit_match_id   TEXT UNIQUE,
  competition_id    TEXT,                   -- -> source_events.id
  region            TEXT,
  team_a            TEXT,
  team_b            TEXT,
  scheduled_at      TEXT,                   -- ISO
  completed_at      TEXT,                   -- ISO, set when final
  status            TEXT,                   -- upcoming/live/final/cancelled/forfeit
  tier              INTEGER,
  faceit_room_url   TEXT,
  state             TEXT NOT NULL DEFAULT 'DISCOVERED',
  capture_status    TEXT NOT NULL DEFAULT 'DISCOVERED',
  data_status       TEXT NOT NULL DEFAULT 'pending',
  raw               TEXT,
  first_seen_at     TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

-- --- YouTube broadcast catalog (Phase C3) ------------------------------
-- One row per discovered video/livestream, independent of any match link
-- (broadcast_candidates below is the many-to-many match-link scoring
-- table; a full-day broadcast can cover several matches, so the video
-- catalog and the link table are deliberately separate).
CREATE TABLE IF NOT EXISTS broadcast_videos (
  video_id            TEXT PRIMARY KEY,     -- platform video id (e.g. YouTube)
  platform            TEXT NOT NULL DEFAULT 'youtube',
  channel_id          TEXT,                 -- -> source_channels.id
  title               TEXT,
  description         TEXT,
  published_at        TEXT,                 -- ISO, upload/publish time
  scheduled_start_at  TEXT,                 -- ISO, liveStreamingDetails.scheduledStartTime
  actual_start_at     TEXT,                 -- ISO, liveStreamingDetails.actualStartTime
  actual_end_at       TEXT,                 -- ISO, liveStreamingDetails.actualEndTime
  live_broadcast_status TEXT,               -- none/upcoming/live/completed
  duration_seconds    INTEGER,
  thumbnail_url       TEXT,
  source_url          TEXT,                 -- https://www.youtube.com/watch?v=<id>
  region              TEXT,
  language            TEXT,
  official_channel    INTEGER NOT NULL DEFAULT 0,
  response_hash       TEXT,                 -- sha256 of the raw API item (audit, not raw storage)
  coverage_state      TEXT NOT NULL DEFAULT 'DISCOVERED',
  discovered_at       TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_broadcast_videos_channel ON broadcast_videos (channel_id);
CREATE INDEX IF NOT EXISTS idx_broadcast_videos_published ON broadcast_videos (published_at);

-- --- Broadcast candidates for a scheduled match (Phase C) -------------
CREATE TABLE IF NOT EXISTS broadcast_candidates (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id          TEXT NOT NULL,          -- -> scheduled_matches.id
  channel_id        TEXT,                   -- -> source_channels.id
  platform          TEXT,
  video_id          TEXT,                   -- e.g. youtube video id
  url               TEXT,
  score             INTEGER NOT NULL DEFAULT 0,
  confidence        TEXT NOT NULL DEFAULT 'low',  -- high/medium/low
  state             TEXT NOT NULL DEFAULT 'DISCOVERED',
  signals           TEXT,                   -- JSON: what contributed to the score
  first_seen_at     TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at        TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (match_id, platform, video_id)
);

-- --- Broadcast segmentation (Phase F) --------------------------------
CREATE TABLE IF NOT EXISTS map_segments (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id            TEXT NOT NULL,
  candidate_match_id  TEXT,                 -- proposed -> scheduled_matches.id
  candidate_map_order INTEGER,
  start_time          REAL,                 -- seconds into the VOD
  end_time            REAL,
  confidence          REAL,
  signals             TEXT,                 -- JSON
  review_status       TEXT NOT NULL DEFAULT 'pending',
  created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (video_id, candidate_match_id, candidate_map_order)
);

-- --- Review queue (Phase H) ------------------------------------------
CREATE TABLE IF NOT EXISTS review_tasks (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  kind              TEXT NOT NULL,          -- segment / composition / broadcast_link / ...
  ref_key           TEXT NOT NULL,          -- points at the thing under review
  lane              TEXT NOT NULL DEFAULT 'rapid', -- auto/rapid/deep (H1)
  state             TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
  payload           TEXT,                   -- JSON: what to show the operator
  created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
  resolved_at       TEXT,
  UNIQUE (kind, ref_key)
);

-- --- Publication runs (Phase I) --------------------------------------
CREATE TABLE IF NOT EXISTS publication_runs (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_key           TEXT UNIQUE,            -- publish:<database-hash>
  db_hash           TEXT,
  prev_db_hash      TEXT,
  export_hash       TEXT,
  prev_export_hash  TEXT,
  branch            TEXT,
  source_commit     TEXT,
  state             TEXT NOT NULL DEFAULT 'PROCESSING',
  revert_command    TEXT,
  created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
  completed_at      TEXT
);

-- --- YouTube API quota accounting (Phase C2) --------------------------
-- Cumulative units spent per UTC day per endpoint, so a run can report
-- "quota used" and a future run can budget the remaining daily allowance
-- (default project quota is 10,000 units/day; videos.list ~= 1 unit,
-- search.list ~= 100 units — see docs/AUTOMATION.md for the full table).
CREATE TABLE IF NOT EXISTS quota_usage (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  day               TEXT NOT NULL,          -- YYYY-MM-DD (UTC)
  endpoint          TEXT NOT NULL,          -- channels.list / playlistItems.list / videos.list / search.list
  units             INTEGER NOT NULL DEFAULT 0,
  calls             INTEGER NOT NULL DEFAULT 0,
  updated_at        TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (day, endpoint)
);

-- --- Rolling coverage snapshots (Phase D4 report history) -------------
CREATE TABLE IF NOT EXISTS coverage_snapshots (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  taken_at          TEXT DEFAULT CURRENT_TIMESTAMP,
  window_days       INTEGER,
  discovered        INTEGER,
  broadcast_located INTEGER,
  downloaded        INTEGER,
  segmented         INTEGER,
  processed         INTEGER,
  published         INTEGER,
  needs_review      INTEGER,
  missing_broadcast INTEGER,
  report            TEXT                    -- JSON: full per-match breakdown
);

-- =====================================================================
-- Generic job queue + attempt history + distributed locks.
-- recording_jobs / processing_jobs from the roadmap are represented as
-- rows in `jobs` distinguished by `kind` (and exposed as the views
-- below), which keeps idempotency (job_key) and failure-retention in ONE
-- place instead of duplicated per job type.
-- =====================================================================
CREATE TABLE IF NOT EXISTS jobs (
  job_key           TEXT PRIMARY KEY,       -- deterministic identity (Phase A2)
  kind              TEXT NOT NULL,          -- discovery/calendar/broadcast/record/process/segment/publish
  state             TEXT NOT NULL DEFAULT 'DISCOVERED',
  priority          INTEGER NOT NULL DEFAULT 0,
  payload           TEXT,                   -- JSON args for the handler
  attempts          INTEGER NOT NULL DEFAULT 0,
  max_attempts      INTEGER,
  -- Failure retention (never lost, per the state-machine section):
  last_error_code   TEXT,
  last_error_message TEXT,
  last_attempt_at   TEXT,
  next_retry_at     TEXT,
  worker_id         TEXT,
  source_url        TEXT,
  diagnostic_path   TEXT,
  created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_kind_state ON jobs (kind, state);
CREATE INDEX IF NOT EXISTS idx_jobs_next_retry ON jobs (next_retry_at);

CREATE TABLE IF NOT EXISTS job_attempts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  job_key           TEXT NOT NULL REFERENCES jobs(job_key) ON DELETE CASCADE,
  attempt           INTEGER NOT NULL,
  worker_id         TEXT,
  ok                INTEGER NOT NULL DEFAULT 0,
  error_code        TEXT,
  error_message     TEXT,
  diagnostic_path   TEXT,
  started_at        TEXT,
  finished_at       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_attempts_key ON job_attempts (job_key);

-- Named views the roadmap asks for; both are just typed slices of `jobs`.
CREATE VIEW IF NOT EXISTS recording_jobs AS
  SELECT * FROM jobs WHERE kind = 'record';
CREATE VIEW IF NOT EXISTS processing_jobs AS
  SELECT * FROM jobs WHERE kind = 'process';

-- --- Distributed locks / leases (Phase A3) ---------------------------
CREATE TABLE IF NOT EXISTS locks (
  resource          TEXT PRIMARY KEY,       -- e.g. 'record:<video-id>'
  worker_id         TEXT NOT NULL,
  acquired_at       TEXT DEFAULT CURRENT_TIMESTAMP,
  heartbeat_at      TEXT DEFAULT CURRENT_TIMESTAMP,
  expires_at        TEXT NOT NULL           -- ISO; a lease past this is stealable
);
