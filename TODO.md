# TODO

Each item below is ready to become a GitHub issue (title = bold heading, description = body text).
Work through the list from top to bottom – later items depend on earlier ones.

---

## 1. Project scaffolding – Flask app factory, config, requirements

Create the initial Python project layout:

- `app/__init__.py` with `create_app(config=None)` application factory
- `config.py` reading all settings from environment variables (`OPENAI_API_BASE`, `OPENAI_API_KEY`, `DATABASE_URL`, `SECRET_KEY`, cron intervals)
- `requirements.txt` with pinned versions: Flask, APScheduler, requests, openai, pytest
- `tests/__init__.py` and a minimal smoke test (`test_app.py`) that confirms `create_app()` returns a Flask app
- `.env.example` listing all required environment variables with placeholder values

Acceptance: `flask run` starts without errors; `pytest tests/` passes.

---

## 2. SQLite database setup with sqlite-vector extension

Set up the database layer:

- `app/db/__init__.py` and `app/db/sqlite.py`:
  - Load the [sqlite-vector](https://github.com/sqliteai/sqlite-vector/blob/main/packages/python/README.md) extension at connection time
  - Expose `get_db()` (returns a connection) and `init_db()` (runs schema SQL)
  - `vector_search(query_embedding, top_k)` helper for nearest-neighbour ontology lookup
- `app/db/schema.sql` with `CREATE TABLE IF NOT EXISTS` statements for `users`, `sessions`, `metadata_cache`, `ontology_terms`, `semantic_tools`
- `flask db init` CLI command that calls `init_db()`

Acceptance: running `flask db init` creates all tables; `vector_search` returns results without error.

---

## 3. User model and Bearer-token authentication

Implement user accounts with token-only authentication:

- `app/models/user.py`:
  - `User` model with `id`, `token` (random URL-safe string), `created_at`, `is_admin` fields
  - `@require_token` decorator that reads `Authorization: Bearer <token>`, looks up the user, and aborts with 401 if missing or invalid
- Admin CLI commands (`flask users create`, `flask users list`, `flask users revoke <token>`)
- No username or password – tokens are the only credential

Acceptance: `flask users create` prints a new token; a request without a valid token returns 401.

---

## 4. HTML session viewer

Create a simple browser-accessible page for inspecting generation sessions:

- `templates/sessions.html` – lists all sessions for the authenticated user, shows status, URL, generated JSON-LD, and log messages
- `app/admin/routes.py` registers a `GET /sessions` route protected by Bearer token (passed as a query param `?token=` for browser convenience)
- `app/models/session.py` – `Session` model with `id`, `user_id`, `url`, `status` (pending / running / done / error), `log`, `result_json`, `created_at`, `updated_at`

Acceptance: opening `/sessions?token=<token>` in a browser shows the user's sessions.

---

## 5. Bioschemas extraction agent

Implement the core LLM agent that extracts Bioschemas metadata from a training-material website:

- `app/agents/bioschemas.py` – `BioschemasExtractorAgent` class with `run(url, prompt=None, llm_client=None)`
  - Fetches web content (respects robots.txt)
  - Decides which links to follow (up to a configurable depth/limit)
  - Performs a self-critical review pass on the draft JSON-LD
  - Validates JSON-LD syntax and required Bioschemas fields
  - Applies TeSS-specific field conventions (in system prompt)
  - Uses `vector_search` for ontology term candidates (EDAM, PaNET)
  - Returns `400`-style signal (raises `NotTrainingContentError`) when the page has no recognisable training content (events or learning materials)
- Unit tests in `tests/test_agents.py` using a mock LLM client and mock HTTP responses

Acceptance: agent returns valid Bioschemas JSON-LD for a sample training URL; raises `NotTrainingContentError` for a non-training URL.

---

## 6. Collection metadata API endpoint

Expose the main endpoint for training collections:

- `app/api/collection.py` – Flask blueprint with `GET /metadata`
  - Query parameters: `url` (required), `prompt` (optional)
  - Requires Bearer token (`@require_token`)
  - Implements the lazy-generation flow:
    1. Return the latest cached result immediately (empty list `[]` if none exists)
    2. Enqueue a background generation task if no task is currently running for this `(user, url)` pair
  - Returns `400` with a plain-text explanation when the URL does not contain training content
  - Response: `Content-Type: application/ld+json`, body is a JSON array of Bioschemas objects

Acceptance: first `GET /metadata?url=<url>` returns `[]`; second call (after generation) returns JSON-LD list; non-training URL returns 400.

---

## 7. Single-resource metadata API endpoint

Expose the endpoint for individual training resources:

- `app/api/resource.py` – Flask blueprint with `GET /metadata/single`
  - Same query parameters and auth as the collection endpoint
  - Same lazy-generation and caching flow
  - Response: `Content-Type: application/ld+json`, body is a single Bioschemas JSON object (not a list)
  - Returns `400` for non-training content

Acceptance: `GET /metadata/single?url=<url>` returns a single JSON-LD object.

---

## 8. Three-level update logic

Add smart update triggering to avoid unnecessary LLM calls:

- In `app/agents/bioschemas.py` and the cron job (`app/cron/metadata.py`):
  - **Level 0 – No update:** hash of fetched content matches stored hash → skip entirely
  - **Level 1 – Incremental update:** content changed → agent receives a summary of the website structure and a diff-hint; focuses only on new/changed items
  - **Level 2 – Full refresh:** triggered by `?force_refresh=true` query param, missing stored hash, or when the agent decides a full re-crawl is needed → metadata regenerated from scratch
- Store content hash and `last_crawled_at` in `metadata_cache` table

Acceptance: unchanged URL skips LLM call; changed URL triggers incremental update; `force_refresh=true` triggers full refresh.

---

## 9. Semantic-tool support (bio.tools, FAIRsharing, …)

Allow the extraction agent to query specialised search-interface websites:

- `app/agents/semantic_tool.py` – `SemanticToolDiscoveryAgent` with `run(tool_url_or_description, user_id, llm_client=None)`:
  - Figures out the API/query format for the given tool (may involve following documentation links)
  - Produces a plain-text _tool description_ (how to construct GET requests, what parameters to use, how to parse results)
  - Stores the description in the `semantic_tools` table (per-user, to avoid leaking info between users)
- `app/cron/tools.py` – periodic job that refreshes stale tool descriptions
- The `BioschemasExtractorAgent` includes relevant tool descriptions in its system prompt when the target URL matches a known tool domain
- Admin UI in `app/admin/routes.py` to add/edit/remove tools per user (URL, description or link to docs)

Acceptance: after running the discovery agent for `https://bio.tools`, the main extraction agent can use bio.tools search in its workflow.

---

## 10. Ontology support (EDAM, PaNET, …)

Index ontology terms in the vector database for fast candidate lookup:

- `app/agents/ontology.py` – `OntologyIndexerAgent` with `run(ontology_url_or_rdf, llm_client=None)`:
  - Fetches RDF/OWL or API data for the ontology
  - Extracts terms (label, description, URI) and embeds them with the configured embedding model
  - Upserts into the `ontology_terms` table using sqlite-vector
- `app/cron/ontologies.py` – periodic job that refreshes ontology indexes
- The `BioschemasExtractorAgent`:
  - Calls `vector_search` to get top-K candidate terms before filling ontology fields
  - Tracks _missing_ ontology terms (label + suggested source URL) in a separate `missing_ontology_terms` table for future curation
- Admin UI to add/remove ontology sources (URL to RDF, SPARQL endpoint, or direct description)

Acceptance: after indexing EDAM, `vector_search("sequence analysis")` returns relevant EDAM terms; missing terms are recorded.

---

## 11. OpenAI-compatible API flexibility and model-selector agent

Make LLM backend fully configurable and self-updating:

- `app/agents/__init__.py` – `get_llm_client(task=None)`:
  - Returns an `openai.OpenAI`-compatible client pointing at `OPENAI_API_BASE`
  - Looks up the preferred model for `task` (e.g. `"extraction"`, `"ontology"`, `"embedding"`) from a `model_assignments` table
  - Falls back to a configurable default model if no assignment exists
- `app/agents/model_selector.py` – `ModelSelectorAgent` with `run(llm_client=None)`:
  - Lists all models available at the configured API endpoint
  - Assigns the best available model to each task type based on name heuristics or a brief capability probe
  - Updates the `model_assignments` table
- `app/cron/metadata.py` (or a new `app/cron/models.py`) – periodic job that runs `ModelSelectorAgent`

Acceptance: changing `OPENAI_API_BASE` to a different provider and restarting updates model assignments automatically on next cron run.

---

## 12. Admin interface for ontologies and semantic tools

Build a simple web admin UI (HTML, no JS framework needed):

- Routes in `app/admin/routes.py` (Bearer token required, admin flag checked):
  - `GET/POST /admin/ontologies` – list and add ontology sources (URL, description, direct RDF link)
  - `GET/POST /admin/ontologies/<id>/delete`
  - `GET/POST /admin/tools` – list and add semantic-tool sources per user
  - `GET/POST /admin/tools/<id>/delete`
  - `POST /admin/ontologies/<id>/reindex` – trigger immediate re-indexing
  - `POST /admin/tools/<id>/rediscover` – trigger immediate tool-description refresh
- Simple `templates/admin_*.html` templates (plain HTML forms, no JS framework)

Acceptance: an admin user can add EDAM ontology and bio.tools via the browser UI and trigger immediate re-indexing.
