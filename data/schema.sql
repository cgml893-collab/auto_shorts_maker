-- AutoShortsMaker: license_lock / device_usage SQLite 스키마
-- MCP sqlite 서버가 data/license_lock.db 에 연결한다.
-- 런타임 앱은 아직 ~/.auto_shorts_maker JSON 을 쓰므로, 이 DB는 AI 점검·집계·마이그레이션용이다.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS license_lock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK (source IN ('desktop', 'mobile')),
    hwid TEXT NOT NULL,
    key_fp TEXT,
    plan TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'basic', 'pro')),
    activated_at TEXT,
    sig TEXT,
    notes TEXT,
    UNIQUE (source, hwid)
);

CREATE TABLE IF NOT EXISTS device_usage (
    hwid TEXT PRIMARY KEY,
    plan TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'basic', 'pro')),
    free_used INTEGER NOT NULL DEFAULT 0,
    key_fp TEXT,
    last_used_at TEXT,
    activated_at TEXT,
    platform TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_license_lock_hwid ON license_lock (hwid);
CREATE INDEX IF NOT EXISTS idx_device_usage_plan ON device_usage (plan);
