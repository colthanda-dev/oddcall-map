-- Oddcall Map schema. Portable between SQLite and Postgres.
-- Three dates, never collapsed: event_date, first_told_date, public_record_date.

CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  category TEXT NOT NULL DEFAULT 'other',          -- uap, cryptid, apparition, sound, light, animal, other
  subcategory TEXT,                                -- mothman, dogman, orb... moderator-normalized
  event_date TEXT,                                 -- ISO date, may be partial per precision
  event_date_precision TEXT NOT NULL DEFAULT 'unknown', -- exact, month, year, unknown
  first_told_date TEXT,
  public_record_date TEXT,
  submitted_at TEXT NOT NULL,
  lat REAL, lng REAL,                              -- exact, moderator-only
  public_lat REAL, public_lng REAL,                -- fuzzed, shown on the map
  county TEXT, state TEXT, place TEXT,             -- state = 2-letter; place = town/crossroads as given
  narrative TEXT NOT NULL,                         -- verbatim, never edited
  redacted_narrative TEXT,                         -- what the public sees, if a redaction was applied
  source_type TEXT NOT NULL,                       -- hotline_sms, hotline_voice, web_form, crawler, field_interview
  source_ref TEXT,                                 -- URL, Twilio SID, interview record
  source_name TEXT,                                -- nuforc, bfro, reddit, rss feed name
  source_excerpt TEXT,                             -- short excerpt for crawler reports (full text stays at source)
  witness_contact TEXT,                            -- moderator-only; see media.py for encryption at rest
  witness_anonymity TEXT NOT NULL DEFAULT 'anonymous', -- anonymous, initials, first_name, full_name
  witness_display TEXT,                            -- what the public sees, per anonymity
  broadcast_consent INTEGER NOT NULL DEFAULT 0,    -- may air on Oddcall Radio
  media TEXT NOT NULL DEFAULT '[]',                -- JSON list of {url, kind}
  credibility_tier TEXT NOT NULL DEFAULT 'witness_anonymous',
  credibility_note TEXT,
  status TEXT NOT NULL DEFAULT 'pending',          -- pending, approved, merged, rejected, redacted
  merged_into TEXT,
  rejection_reason TEXT,
  moderator_notes TEXT,
  approved_at TEXT,
  hoax_flags TEXT NOT NULL DEFAULT '[]',           -- JSON list of flags raised at intake
  ip_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_state_county ON reports(state, county);
CREATE INDEX IF NOT EXISTS idx_reports_pub ON reports(public_lat, public_lng);
CREATE INDEX IF NOT EXISTS idx_reports_event ON reports(event_date);
CREATE INDEX IF NOT EXISTS idx_reports_source_ref ON reports(source_ref);

CREATE TABLE IF NOT EXISTS moderators (
  email TEXT PRIMARY KEY,
  name TEXT,
  created_at TEXT NOT NULL,
  last_login TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY,
  report_id TEXT,
  actor TEXT NOT NULL,            -- moderator email or 'system'
  action TEXT NOT NULL,           -- approve, reject, merge, redact, fuzz, tier, import, takedown...
  detail TEXT,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_report ON audit_log(report_id);

CREATE TABLE IF NOT EXISTS subscriptions (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  state TEXT, county TEXT,        -- county alert
  lat REAL, lng REAL, radius_km REAL, -- or radius alert
  confirmed INTEGER NOT NULL DEFAULT 0,
  token TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  name TEXT PRIMARY KEY,          -- crawler adapter name
  kind TEXT,
  watermark TEXT,                 -- last imported date/id, per source
  last_run TEXT,
  last_count INTEGER
);

-- Hotline conversations in progress: the four follow-up questions by text.
CREATE TABLE IF NOT EXISTS hotline_sessions (
  phone TEXT PRIMARY KEY,
  step INTEGER NOT NULL DEFAULT 0,
  draft TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS geocode_cache (
  key TEXT PRIMARY KEY,
  county TEXT, state TEXT, place TEXT,
  at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limits (
  key TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate ON rate_limits(key, at);
