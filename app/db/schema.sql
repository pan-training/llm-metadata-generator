-- users: Bearer-token auth, no username/password
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT    NOT NULL UNIQUE,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    is_admin   INTEGER NOT NULL DEFAULT 0
);

-- sessions: tracks background generation state per (user, url)
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    url         TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    log         TEXT,
    result_json TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- metadata_cache: URL-level crawl state shared across users.
-- Stores only the content hash and structural summary used by the three-level
-- update logic.  Per-user results (which may differ because of individual
-- prompts) are stored in sessions.result_json, not here.
CREATE TABLE IF NOT EXISTS metadata_cache (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    url                TEXT    NOT NULL UNIQUE,
    content_hash       TEXT,
    structural_summary TEXT,
    last_crawled_at    TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- semantic_tools: globally admin-managed tool descriptions (bio.tools, FAIRsharing, …)
CREATE TABLE IF NOT EXISTS semantic_tools (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    admin_description TEXT,
    short_summary    TEXT,
    full_description TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
