-- Sanitized production schema snapshot.

PRAGMA foreign_keys=OFF;

BEGIN;

CREATE TABLE callback_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          video_id TEXT NOT NULL DEFAULT '',
          publisher TEXT NOT NULL,
          platform TEXT NOT NULL,
          views INTEGER NOT NULL DEFAULT 0,
          clicks INTEGER NOT NULL DEFAULT 0,
          registrations INTEGER NOT NULL DEFAULT 0,
          extra_data TEXT NOT NULL DEFAULT '{}',
          callback_at TEXT NOT NULL
        );

CREATE TABLE candidates (
          id TEXT PRIMARY KEY,
          parent_id TEXT,
          platform TEXT NOT NULL,
          source_id TEXT,
          url TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          duration REAL,
          view_count INTEGER NOT NULL DEFAULT 0,
          detected_language TEXT,
          score REAL NOT NULL DEFAULT 0,
          status TEXT NOT NULL,
          published_flag INTEGER NOT NULL DEFAULT 0,
          metadata_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

CREATE TABLE conversion_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          platform TEXT NOT NULL DEFAULT '',
          hook_version TEXT NOT NULL DEFAULT '',
          event_type TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          visitor_id TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}'
        );

CREATE TABLE download_claims (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          asset_id TEXT NOT NULL DEFAULT '',
          filename TEXT NOT NULL DEFAULT '',
          variant TEXT NOT NULL DEFAULT '',
          publisher TEXT NOT NULL,
          publish_platform TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          downloaded_at TEXT NOT NULL,
          metrics_updated_at TEXT,
          views INTEGER NOT NULL DEFAULT 0,
          clicks INTEGER NOT NULL DEFAULT 0,
          registrations INTEGER NOT NULL DEFAULT 0,
          extra_data TEXT NOT NULL DEFAULT '{}'
        );

CREATE TABLE events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

CREATE TABLE feedback_actions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT,
          keyword TEXT NOT NULL DEFAULT '',
          action_type TEXT NOT NULL,
          reason TEXT NOT NULL,
          score REAL NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'PROPOSED',
          created_at TEXT NOT NULL
        );

CREATE TABLE performance_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          platform TEXT NOT NULL,
          captured_at TEXT NOT NULL,
          views INTEGER NOT NULL DEFAULT 0,
          likes INTEGER NOT NULL DEFAULT 0,
          comments INTEGER NOT NULL DEFAULT 0,
          shares INTEGER NOT NULL DEFAULT 0,
          clicks INTEGER NOT NULL DEFAULT 0,
          installs INTEGER NOT NULL DEFAULT 0,
          registrations INTEGER NOT NULL DEFAULT 0
        );

CREATE TABLE publications (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          platform TEXT NOT NULL,
          account TEXT NOT NULL DEFAULT '',
          scheduled_at TEXT,
          published_at TEXT,
          status TEXT NOT NULL DEFAULT 'QUEUED',
          post_url TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(candidate_id, platform, account, scheduled_at)
        );

CREATE TABLE render_jobs (
          id TEXT PRIMARY KEY,
          candidate_id TEXT NOT NULL,
          variant TEXT NOT NULL DEFAULT '',
          engine TEXT NOT NULL,
          status TEXT NOT NULL,
          progress REAL NOT NULL DEFAULT 0,
          output_path TEXT NOT NULL DEFAULT '',
          cancel_file TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

CREATE TABLE seen_sources (
          platform TEXT NOT NULL,
          source_key TEXT NOT NULL,
          source_id TEXT,
          url TEXT NOT NULL DEFAULT '',
          first_candidate_id TEXT NOT NULL DEFAULT '',
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          PRIMARY KEY(platform, source_key)
        );

CREATE TABLE workers (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          role TEXT NOT NULL,
          host TEXT NOT NULL,
          status TEXT NOT NULL,
          current_job TEXT NOT NULL DEFAULT '',
          last_seen TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );

CREATE INDEX callback_at
          ON callback_logs(callback_at);

CREATE INDEX callback_candidate
          ON callback_logs(candidate_id);

CREATE UNIQUE INDEX candidates_platform_source
          ON candidates(platform, source_id) WHERE source_id IS NOT NULL;

CREATE INDEX conversion_candidate_type
          ON conversion_events(candidate_id, event_type, occurred_at DESC);

CREATE INDEX download_claim_candidate
          ON download_claims(candidate_id, downloaded_at DESC);

CREATE INDEX performance_candidate_platform
          ON performance_snapshots(candidate_id, platform, captured_at DESC);

CREATE INDEX render_jobs_candidate
          ON render_jobs(candidate_id, updated_at DESC);

COMMIT;
