CREATE TABLE IF NOT EXISTS youtube_channel_auths (
  account TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL DEFAULT '',
  channel_title TEXT NOT NULL DEFAULT '',
  scopes TEXT NOT NULL DEFAULT '',
  encrypted_refresh_token TEXT NOT NULL DEFAULT '',
  token_type TEXT NOT NULL DEFAULT '',
  expires_in INTEGER NOT NULL DEFAULT 0,
  authorized_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS publications (
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

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
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
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
