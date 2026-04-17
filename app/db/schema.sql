-- users: one row per API user; authentication is Bearer-token only (no password).
-- is_admin=1 grants access to admin routes (tool management, user creation).
-- token_hash stores the SHA-256 hex digest of the raw Bearer token; the plaintext
-- token is never stored so a database leak does not expose usable credentials.
-- Typical queries:
--   Validate a request:  SELECT id, is_admin FROM users WHERE token_hash = ?
--   Create a user:       INSERT INTO users (token_hash) VALUES (?)
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT    NOT NULL UNIQUE,
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

-- ontology_sources: admin-provided ontology ingestion definitions.
-- description may include plain text and links to RDF/OWL files, docs, SPARQL endpoints.
CREATE TABLE IF NOT EXISTS ontology_sources (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    description       TEXT    NOT NULL,
    rdf_url           TEXT,
    documentation_url TEXT,
    active_version_id INTEGER REFERENCES ontology_index_versions(id),
    last_indexed_at   TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ontology_index_versions: immutable snapshots for rollback/history.
CREATE TABLE IF NOT EXISTS ontology_index_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER NOT NULL REFERENCES ontology_sources(id) ON DELETE CASCADE,
    embedding_model TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'ready',
    notes           TEXT,
    is_active       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ontology_terms: vector-indexed terms per source/version.
-- embedding_json stores numeric vectors. sqlite-vector loading is configured in app/db/sqlite.py.
CREATE TABLE IF NOT EXISTS ontology_terms (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER NOT NULL REFERENCES ontology_sources(id) ON DELETE CASCADE,
    version_id      INTEGER NOT NULL REFERENCES ontology_index_versions(id) ON DELETE CASCADE,
    label           TEXT    NOT NULL,
    description     TEXT,
    uri             TEXT    NOT NULL,
    ontology_name   TEXT    NOT NULL,
    properties_json TEXT,
    embedding_json  TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(version_id, uri)
);

CREATE INDEX IF NOT EXISTS idx_ontology_terms_version ON ontology_terms(version_id);
CREATE INDEX IF NOT EXISTS idx_ontology_terms_label ON ontology_terms(label);

-- missing_ontology_terms: concepts detected in extraction but missing from indexed ontologies.
CREATE TABLE IF NOT EXISTS missing_ontology_terms (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    label                TEXT    NOT NULL,
    description          TEXT,
    ontology_name        TEXT,
    suggested_source_url TEXT,
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_missing_ontology_terms_label_ontology
ON missing_ontology_terms(lower(label), coalesce(lower(ontology_name), ''));

-- many-to-many: missing ontology terms <-> metadata_cache rows that would benefit.
CREATE TABLE IF NOT EXISTS missing_ontology_term_links (
    missing_term_id  INTEGER NOT NULL REFERENCES missing_ontology_terms(id) ON DELETE CASCADE,
    metadata_cache_id INTEGER NOT NULL REFERENCES metadata_cache(id) ON DELETE CASCADE,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (missing_term_id, metadata_cache_id)
);

-- app_settings: small key/value storage for startup checks (e.g. embedding model changes).
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
