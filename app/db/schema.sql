-- users: one row per API user; authentication is Bearer-token only (no password).
-- is_admin=1 grants access to admin routes (tool management, user creation).
-- Typical queries:
--   Validate a request:  SELECT id, is_admin FROM users WHERE token = ?
--   Create a user:       INSERT INTO users (token) VALUES (?)
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT    NOT NULL UNIQUE,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    is_admin   INTEGER NOT NULL DEFAULT 0
);

-- sessions: one row per background generation job, scoped to a (user, url) pair.
-- status progresses: 'pending' → 'running' → 'done' | 'error'.
-- result_json holds the Bioschemas JSON-LD produced by that run; it may differ
-- between users because each user can supply an optional prompt that steers the
-- extraction agent.
-- Typical queries:
--   Latest result for a user+url:  SELECT result_json FROM sessions
--                                    WHERE user_id=? AND url=? AND status='done'
--                                    ORDER BY updated_at DESC LIMIT 1
--   All active jobs:               SELECT * FROM sessions WHERE status IN ('pending','running')
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

-- metadata_cache: one row per URL, shared across all users.
-- Stores only the URL-level crawl state used by the three-level update logic:
--   content_hash       – hash of the fetched page content; unchanged hash → skip (level 0).
--   structural_summary – compact summary of the last full crawl given to the agent
--                        for incremental updates (level 1).
-- Per-user results (which may differ due to individual prompts) are NOT stored
-- here; they live in sessions.result_json.
-- Typical queries:
--   Check if a re-crawl is needed:  SELECT content_hash, structural_summary
--                                     FROM metadata_cache WHERE url = ?
--   Upsert after a crawl:           INSERT INTO metadata_cache (url, content_hash, ...)
--                                     VALUES (?, ?, ...) ON CONFLICT(url) DO UPDATE SET ...
CREATE TABLE IF NOT EXISTS metadata_cache (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    url                TEXT    NOT NULL UNIQUE,
    content_hash       TEXT,
    structural_summary TEXT,
    last_crawled_at    TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- semantic_tools: globally admin-managed descriptions of external tools
-- (e.g. bio.tools, FAIRsharing entries).  Tool descriptions are included in the
-- extraction agent's system prompt so the agent is aware of available tools.
-- short_summary is always sent to the agent; full_description is fetched on demand.
-- admin_description is a free-text note visible only to admins (not sent to the LLM).
-- Typical queries:
--   All short summaries for system prompt:  SELECT name, short_summary FROM semantic_tools
--   Full description when agent requests:   SELECT full_description FROM semantic_tools WHERE name = ?
CREATE TABLE IF NOT EXISTS semantic_tools (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    admin_description TEXT,
    short_summary     TEXT,
    full_description  TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);
