# TODO

Each item below is ready to become a GitHub issue (title = bold heading, description = body text).
Work through the list from top to bottom – later items depend on earlier ones.

---

## 1. Project scaffolding – Flask app factory, config, Poetry setup, and SQLite database

Create the initial Python project layout including the database layer so the first working slice is in place from the start:

**App factory & config:**
- `app/__init__.py` with `create_app(config=None)` application factory
- `config.py` reading all settings from environment variables (`OPENAI_API_BASE`, `OPENAI_API_KEY`, `DATABASE_URL`, `SECRET_KEY`, cron intervals)
- `pyproject.toml` with Poetry project definition and pinned dependencies: Flask, APScheduler, requests, openai, pytest
- `.env.example` listing all required environment variables with placeholder values

**Database layer:**
- `app/db/__init__.py` and `app/db/sqlite.py`:
  - Expose `get_db()` (returns a connection) and `init_db()` (runs schema SQL)
  - `app/db/schema.sql` with `CREATE TABLE IF NOT EXISTS` statements for `users`, `sessions`, `metadata_cache`, `semantic_tools`
  - `app/migrations/` directory for future plain-SQL migration scripts
- `flask db init` CLI command that calls `init_db()`

**Tests:**
- `tests/__init__.py` and a minimal smoke test (`test_app.py`) that confirms `create_app()` returns a Flask app and `flask db init` creates the expected tables

Acceptance: `flask run` starts without errors; `flask db init` creates all tables; `pytest tests/` passes.

---

## 2. User model and Bearer-token authentication

Implement user accounts with token-only authentication:

- `app/models/user.py`:
  - `User` model with `id`, `token` (random URL-safe string), `created_at`, `is_admin` fields
  - `@require_token` decorator that reads `Authorization: Bearer <token>`, looks up the user, and aborts with 401 if missing or invalid
- Admin CLI commands (`flask users create`, `flask users list`, `flask users revoke <token>`)
- No username or password – tokens are the only credential
- Add a simple `/ping` endpoint (returns 200 with `{"ok": true}`) protected by `@require_token` so the decorator can be exercised in tests without any other feature being implemented

Acceptance: `flask users create` prints a new token; a request without a valid token returns 401; `/ping` returns 200 with a valid token.

---

## 3. Core Bioschemas extraction – both API endpoints and basic agent

Implement the core LLM-based extraction and expose both API endpoints in a single issue so there is something end-to-end to test immediately:

**Agent (`app/agents/bioschemas.py` – `BioschemasExtractorAgent`):**
- `run(url, prompt=None, llm_client=None, mode="collection")` where `mode` is `"collection"` or `"single"`
- Fetches web content (respects robots.txt)
- Decides which links to follow (up to a configurable depth/limit)
- Performs a self-critical review pass on the draft JSON-LD
- Validates JSON-LD syntax and required Bioschemas fields
- Applies TeSS-specific field conventions (system prompt lives in this file)
- Notes any ontology terms that appear missing and would benefit from future vector search – stores them as a placeholder comment for now; the actual `vector_search` integration comes in the ontology issue
- Raises `NotTrainingContentError` (400-level signal) when the page has no recognisable training content. Also raises `ContentAccessError` when robots.txt or server permissions prevented fetching enough content to make a judgement.
  - Example 400 case: robots.txt blocking all crawlers prevents access to the main content source

**Collection endpoint (`app/api/collection.py`):**
- `GET /metadata?url=<url>[&prompt=<prompt>]` protected by `@require_token`
- Lazy-generation flow:
  1. Return latest cached result immediately (empty list `[]` if none)
  2. Enqueue a background generation task if none is running for this `(user, url)` pair
- Response: `Content-Type: application/ld+json`, body is a JSON array

**Single-resource endpoint (`app/api/resource.py`):**
- `GET /metadata/single?url=<url>[&prompt=<prompt>]` – same auth and lazy-generation flow
- Response: single Bioschemas JSON object (not a list)

**Session model (`app/models/session.py`):**
- `id`, `user_id`, `url`, `status` (pending / running / done / error), `log`, `result_json`, `created_at`, `updated_at`

Acceptance: first `GET /metadata?url=<url>` returns `[]`; second call (after generation) returns JSON-LD list; non-training URL returns 400; robots.txt-blocked URL returns 400 with a distinct message.

---

## 4. HTML session viewer and secure login

Create a browser-accessible page for inspecting generation sessions with a secure login flow:

- `POST /login` – accepts a Bearer token in the form body, validates it, and sets a server-side browser session (cookie-based). Never pass the token as a GET query parameter.
- `GET /sessions` – protected by the browser session (redirect to login if not authenticated); lists all sessions for the logged-in user showing status, URL, generated JSON-LD, and log messages
- `templates/sessions.html` and `templates/login.html`
- `app/models/session.py` is already in place from the previous issue; this issue adds the viewer routes

Acceptance: visiting `/sessions` without a session redirects to `/login`; POSTing a valid token at `/login` establishes a session and redirects to `/sessions`.

---

## 5. Three-level update logic and background cron

Add smart update triggering to avoid unnecessary LLM calls:

- In `app/agents/bioschemas.py` and `app/cron/metadata.py`:
  - **Level 0 – No update:** hash of fetched content matches stored hash → skip entirely
  - **Level 1 – Incremental update:** content hash changed, or the agent detects that the website structure has changed since the stored summary was generated → agent receives the stored structure summary and focuses only on new/changed items
  - **Level 2 – Full refresh:** stored hash is missing, or a rare random roll fires (very infrequently, to guard against accumulated drift from incremental updates) → metadata regenerated from scratch
  - Full refresh is **not** triggered by a query parameter – it is programmatic or random
- Store content hash, structure summary, and `last_crawled_at` in `metadata_cache` table
- `app/cron/metadata.py` – `register(scheduler)` sets up the periodic metadata refresh job

Acceptance: unchanged URL skips LLM call; changed URL triggers incremental update; missing hash triggers full refresh; random roll occasionally triggers full refresh.

---

## 6. Semantic-tool support (bio.tools, FAIRsharing, …) – admin-managed

Allow the extraction agent to query specialised search-interface websites. Tools are configured by an admin, not per user.

**Admin data model:**
- `semantic_tools` table: `id`, `name`, `short_description` (≤2 sentences, always included in system prompt), `full_description` (full how-to-use text, fetched on demand when agent decides to use the tool), `source_url`, `last_refreshed_at`
- Admin UI in `app/admin/routes.py` (admin token required):
  - `GET/POST /admin/tools` – list and add tools (name, source URL or direct description)
  - `GET/POST /admin/tools/<id>/edit` and `/delete`
  - `POST /admin/tools/<id>/rediscover` – trigger immediate description refresh

**Discovery agent (`app/agents/semantic_tool.py` – `SemanticToolDiscoveryAgent`):**
- `run(tool_source, llm_client=None)` where `tool_source` is a URL to documentation, a direct description, or a link to the tool itself
- Generates both `short_description` and `full_description` for the tool
- Stores results in `semantic_tools` table

**Integration with extraction agent:**
- `BioschemasExtractorAgent` always includes all `short_description` values in its system prompt
- When the agent signals it wants to use a tool, it receives that tool's `full_description`
- The agent should also track when it last used each semantic tool and consider re-querying if the tool's `last_refreshed_at` indicates new information may be available

**Cron (`app/cron/tools.py`):**
- Periodic job that re-runs `SemanticToolDiscoveryAgent` for stale tools

Acceptance: after running the discovery agent for `https://bio.tools`, the main extraction agent can use bio.tools search in its workflow.

---

## 7. Ontology support (EDAM, PaNET, …) and vector search integration

Index ontology terms in the vector database for fast candidate lookup. The sqlite-vector extension setup belongs here since this is where it is first actually used.

**Vector extension setup:**
- Load the sqlite-vector extension in `app/db/sqlite.py` at connection time
- `vector_search(query_embedding, top_k)` helper for nearest-neighbour lookup
- Add `ontology_terms` and `missing_ontology_terms` tables to `app/db/schema.sql`

**Index history and rollback:**
- Keep a history of index snapshots in `ontology_index_snapshots` table (timestamp, model used, row count) so an admin can roll back to a previous index version via the admin UI
- When the embedding model changes, a full reindex is mandatory and must complete before any training-material metadata updates run

**Ontology indexer agent (`app/agents/ontology.py` – `OntologyIndexerAgent`):**
- `run(ontology_source, llm_client=None)` where `ontology_source` is a description that may include URLs to ontology documentation, RDF/OWL files, or SPARQL endpoints
- Extracts terms (label, description, URI) and embeds them
- Upserts into `ontology_terms` using sqlite-vector
- Records an index snapshot entry after each successful run

**Missing ontology terms tracking:**
- `missing_ontology_terms` table: `id`, `label`, `description`, `ontology` (which ontology this term would belong to), `created_at`
- Many-to-one join table `missing_term_materials`: links each missing term to the training materials/events that would benefit from it
- Before inserting a new missing term, search by label first; if found, just add an additional material link instead of a duplicate entry

**Integration with extraction agent:**
- `BioschemasExtractorAgent` calls `vector_search` to get top-K candidate ontology terms before filling ontology fields (replaces the placeholder comment from issue 3)
- Records any terms the agent suggests but can't find in the index into `missing_ontology_terms`

**Cron (`app/cron/ontologies.py`):**
- Periodic job that runs `OntologyIndexerAgent` for registered ontologies

**Admin UI:**
- `GET/POST /admin/ontologies` – list and add ontology sources (name, description including any relevant URLs or RDF links)
- `GET/POST /admin/ontologies/<id>/edit` and `/delete`
- `POST /admin/ontologies/<id>/reindex` – trigger immediate re-indexing
- `GET /admin/ontologies/<id>/snapshots` – view index snapshot history and trigger rollback

Acceptance: after indexing EDAM, `vector_search("sequence analysis")` returns relevant EDAM terms; missing terms are recorded with linked materials; admin can roll back the index.

---

## 8. OpenAI-compatible API flexibility and model-selector agent

Make the LLM backend fully configurable and self-updating.

**Fine-grained task mapping:**
- Task names: `"page_filtering"` (detecting irrelevant/minified content), `"summarization"` (summarising page sections), `"link_selection"` (deciding which links to follow), `"extraction"` (generating JSON-LD), `"jsonld_review"` (reviewing and correcting the draft), `"embedding"` (vector embeddings for ontology search)
- These task types should already be wired into the extraction and ontology agents from earlier issues; this issue formalises the routing

**`get_llm_client(task)` (`app/agents/__init__.py`):**
- Returns an `openai.OpenAI`-compatible client pointing at `OPENAI_API_BASE`
- Looks up the preferred model for `task` from a `model_assignments` table
- Falls back to a configurable default model if no assignment exists

**`ModelSelectorAgent` (`app/agents/model_selector.py`):**
- `run(llm_client=None)`:
  - Lists all models available at the configured API endpoint
  - Optionally queries a model listing website/API if available
  - Runs brief capability probes for each task type and records response time
  - Assigns the best available model to each task based on probe results and timing
  - Updates `model_assignments` table
- Index history and rollback: keep a `model_assignment_snapshots` table so an admin can roll back to a previous assignment set if a newly selected model performs poorly or is no longer available
- Admin UI: `GET /admin/models` shows current assignments, snapshot history, and allows manual overrides or admin comments per model; `POST /admin/models/<id>/rollback` restores a snapshot

**Cron (`app/cron/models.py`):**
- Periodic job that runs `ModelSelectorAgent`

Acceptance: changing `OPENAI_API_BASE` to a different provider and restarting updates model assignments automatically; admin can override or roll back assignments.

---

## 9. Testing and quality refinement

Extend the test suite and refine agent behaviour based on end-to-end experience:

- Ensure every feature added in issues 1–8 has meaningful test coverage (unit + integration)
- Add edge-case tests: malformed URLs, rate-limited sites, very large pages, ontology RDF parse errors
- Refine agent prompts based on observed extraction quality
- Document any tuning decisions in `app/agents/bioschemas.py` (inline comments)

Acceptance: `pytest tests/` passes with ≥80% coverage; no known regressions.
