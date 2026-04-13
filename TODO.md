# TODO

Each item below is ready to become a GitHub issue (title = bold heading, description = body text).
Work through the list from top to bottom – later items depend on earlier ones.
Items marked **✅ Done** have been fully implemented.

---

## ✅ 1. Project scaffolding – Flask app factory, config, and database setup

Create the initial Python project layout including the database schema (without the sqlite-vector extension, which is deferred to the ontology issue):

- `pyproject.toml` using [Poetry](https://python-poetry.org/) with pinned dependencies: Flask, APScheduler, requests, openai, pytest
- `.env.example` listing all required environment variables with placeholder values
- `app/__init__.py` with `create_app(config=None)` application factory
- `config.py` reading all settings from environment variables (`OPENAI_API_BASE`, `OPENAI_API_KEY`, `DATABASE_URL` defaulting to `data/metadata.db`, `SECRET_KEY`, cron intervals)
- `app/db/__init__.py` and `app/db/sqlite.py`:
  - Expose `get_db()` (returns a connection) and `init_db()` (runs `schema.sql`)
  - **Do not** load the sqlite-vector extension yet – that is done when ontologies are implemented
- `app/db/schema.sql` with `CREATE TABLE IF NOT EXISTS` for `users`, `sessions`, `metadata_cache`, `semantic_tools` (no `ontology_terms` table yet)
- `flask db init` CLI command that calls `init_db()`
- `tests/__init__.py` and a smoke test `tests/test_app.py` confirming `create_app()` returns a Flask app

Acceptance: `flask run` starts without errors; `pytest tests/` passes; `flask db init` creates all tables.

---

## ✅ 2. User model and Bearer-token authentication

Implement user accounts with token-only authentication:

- `app/models/user.py`:
  - `User` model with `id`, `token` (random URL-safe string), `created_at`, `is_admin` fields
  - `@require_token` decorator that reads `Authorization: Bearer <token>`, looks up the user, and aborts with `401` if missing or invalid
- Admin CLI commands: `flask users create`, `flask users list`, `flask users revoke <token>`
- No username or password – tokens are the only credential
- Add a simple `GET /health` endpoint (no auth required) and a `GET /whoami` endpoint (auth required) to allow easy manual and automated testing of the decorator without needing a full feature endpoint

Acceptance: `flask users create` prints a new token; `GET /whoami` with a valid token returns user info; without a token it returns `401`.

---

## ✅ 3. Core working system: Bioschemas extraction agent and API endpoints

Implement the minimum end-to-end working system: both API endpoints, the extraction agent, and session tracking, plus the session viewer so results can be inspected immediately.

### Extraction agent (`app/agents/bioschemas.py`)

The agent uses a four-phase chunk-based pipeline to handle arbitrarily large
websites without overflowing the LLM context window:

1. **CRAWL + DISCOVER** (tree-like, chunk-by-chunk): Fetch page → strip noise →
   split into overlapping text chunks → per-chunk LLM call classifies relevance,
   finds training items, and identifies follow links in one call.  Follows links
   recursively up to `MAX_FOLLOW_DEPTH`, respecting `robots.txt` (with per-run
   caching per domain).
2. **EXTRACT** (per item, separate context windows): For each discovered item,
   fetch its detail page if available, strip and chunk it, call the quality LLM
   to extract a full Bioschemas JSON-LD object.
3. **REVIEW** (per item): Self-critical LLM review pass.
4. **VALIDATE + FIX**: Validate against `docs/Bioschemas/bioschemas-training-schema.json`
   (via `jsonschema`), feed errors back to LLM for a single fixing pass.
   Programmatic TeSS conventions applied last (`@context`, `dct:conformsTo`, `@id`).

Structural summary (stored in `metadata_cache.structural_summary`) records crawled
page hashes and URL patterns — used on next run to skip unchanged content.

- Raises `AccessDeniedError` if primary URL is blocked by `robots.txt` or returns 401/403.
- Raises `NotTrainingContentError` when no training content found.

Future improvements (not yet implemented — see items below):
- **3c** Structured agent logger with typed events (info/warn/llm_call/…) for
  richer display in the session viewer (replaces the current string-based `log_fn`).
  See issue 8 below.
- **3d** Per-item incremental updates: compare per-item content hash against stored
  result to skip re-extraction of unchanged items (builds on issue 4).

### API endpoints (`app/api/collection.py`, `app/api/resource.py`)

Both endpoints share the same lazy-generation flow and differ only in whether they return a list or a single object:

- `GET /metadata?url=<url>[&prompt=<prompt>]` – Bioschemas JSON-LD list for a training collection
- `GET /metadata/single?url=<url>[&prompt=<prompt>]` – single Bioschemas JSON-LD object for one resource
- Both require Bearer token (`@require_token`)
- Flow: return the latest cached result immediately (empty list / `{}` if none), then enqueue a background generation task if none is already running for this `(user, url)` pair
- Return `400` with a plain-text explanation for `NotTrainingContentError`
- Return `403` with a plain-text explanation for `AccessDeniedError` (e.g. blocked by `robots.txt`)
- Response: `Content-Type: application/ld+json`

### Session model and viewer

- `app/models/session.py` – `Session` model: `id`, `user_id`, `url`, `status` (pending / running / done / error), `log`, `result_json`, `created_at`, `updated_at`
- `templates/sessions.html` – lists the authenticated user's sessions with status, URL, generated JSON-LD and log messages; designed with the agent output in mind so useful fields are visible at a glance
- `POST /sessions/login` – accepts a JSON body `{"token": "..."}` and sets a signed session cookie (tokens must **never** appear in GET query parameters)
- `GET /sessions` – protected by session cookie; shows the session viewer

### Tests

- `tests/test_api.py` – test lazy-generation flow, 400/403 responses, auth
- `tests/test_agents.py` – mock LLM client + mock HTTP, test happy path and `NotTrainingContentError`

Acceptance: end-to-end: first `GET /metadata?url=<url>` returns `[]`; after background generation completes, second call returns JSON-LD list; non-training URL returns `400`; robots.txt-blocked URL returns `403`; session viewer shows the result.

---

## 4. Three-level update logic

Add smart update triggering to avoid unnecessary LLM calls:

- In `app/agents/bioschemas.py` and the cron job (`app/cron/metadata.py`):
  - **Level 0 – No update:** hash of fetched content matches stored hash → skip entirely.
  - **Incremental update:** hash changed → agent receives the stored structural summary
    (crawled page hashes + URL patterns) and skips pages whose hashes match; focuses
    only on new/changed items.  Records the timestamp of the last semantic-tool search
    used during extraction so the agent can decide whether a new tool search is warranted
    on future runs.
  - **Full refresh:** triggered (a) randomly at a very low probability (~1 % of cron runs)
    to catch long-term drift, (b) when no stored hash exists. The `force_refresh` query
    parameter is available for admin/debugging use only — computer agents never set it.
- Store content hash, `last_crawled_at`, and a `structural_summary` in `metadata_cache`

Acceptance: unchanged URL skips LLM call; changed URL triggers incremental; ~1 % of runs trigger full refresh.

---

## 5. Semantic-tool support (bio.tools, FAIRsharing, …)

Allow the extraction agent to query specialised search-interface websites. Tools are **globally admin-managed** (not per-user).

### Discovery agent (`app/agents/semantic_tool.py`)

- `SemanticToolDiscoveryAgent` with `run(description, llm_client=None)` where `description` is a free-text admin-provided description that may contain links to tool documentation, direct tool API descriptions, or links to the tool itself
  - Figures out how to construct GET requests to the tool (parameters, URL format, response parsing)
  - Produces a structured output:
    - **Short summary** (one sentence) – always included in the extraction agent's system prompt so it is aware of all available tools
    - **Full detailed description** – stored separately; provided to the agent only when it explicitly decides to use this tool
  - Stores both in the `semantic_tools` table

### Integration with extraction agent

- The extraction agent's system prompt always includes the short summary of every configured tool
- When the agent decides to use a tool, it requests the full detailed description (on demand, not upfront)

### Admin UI (added to `app/admin/routes.py`)

- `GET/POST /admin/tools` – list and add tools (admin-provided description field; may include URLs or direct API docs)
- `GET/POST /admin/tools/<id>/delete`
- `POST /admin/tools/<id>/rediscover` – trigger immediate re-discovery
- Keep a history of tool description versions for each tool so the admin can roll back to a previous description if a re-discovery produces worse results
- Simple HTML templates (`templates/admin_tools.html`)

### Cron

- `app/cron/tools.py` – periodic job that refreshes stale tool descriptions

Acceptance: after running the discovery agent for bio.tools, the main extraction agent is aware of it (short summary in prompt) and can request the full description to perform a search.

---

## 6. Ontology support (EDAM, PaNET, …)

Index ontology terms in the vector database for fast candidate lookup. **This is the issue where the sqlite-vector extension is set up.**

### Database changes

- Load the [sqlite-vector](https://github.com/sqliteai/sqlite-vector/blob/main/packages/python/README.md) extension in `app/db/sqlite.py`
- Add `ontology_terms` and `missing_ontology_terms` tables to `app/db/schema.sql`
- Add `vector_search(query_embedding, top_k)` helper to `app/db/sqlite.py`
- `missing_ontology_terms` schema: `id`, `label`, `description`, `ontology_name`, `suggested_source_url`, plus a many-to-many join table linking each missing term to one or more `metadata_cache` records (training materials / events) that would benefit from it. Before inserting a new missing term, check whether a matching label already exists and if so just add the new training-material link.

### Ontology indexer agent (`app/agents/ontology.py`)

- `OntologyIndexerAgent` with `run(description, llm_client=None)` where `description` is a free-text admin-provided field that may contain links to RDF/OWL files, SPARQL endpoints, or a direct textual description of the ontology
  - Fetches and parses ontology terms (label, description, URI)
  - Embeds terms using the `ontology_embedding` task model
  - Upserts into `ontology_terms` using sqlite-vector
  - Keeps a versioned index history so the admin can roll back to a previous index snapshot if needed (e.g. if a re-index produces worse results)
- **Important:** if the embedding model changes, all existing `ontology_terms` must be re-indexed before any metadata extraction runs. Add a startup check in `create_app` that detects embedding model changes and triggers an automatic re-index.

### Integration with extraction agent

- Add vector-search capability to `BioschemasExtractorAgent` (wiring in the placeholder left in issue 3): call `vector_search` to get top-K candidate ontology terms before filling ontology fields
- The agent also tracks missing ontology terms: if a concept clearly belongs in an ontology but no matching term is found, record it in `missing_ontology_terms` (label, description, which ontology it would belong to, link to the training material)

### Admin UI (added to `app/admin/routes.py`)

- `GET/POST /admin/ontologies` – list and add ontology sources (admin-provided description field)
- `GET/POST /admin/ontologies/<id>/delete`
- `POST /admin/ontologies/<id>/reindex` – trigger immediate re-indexing; previous index version is kept for rollback
- `GET /admin/ontologies/<id>/history` – view index version history and roll back
- `GET /admin/missing-terms` – browse missing ontology term suggestions grouped by ontology, with links to the training materials that need them
- Simple HTML templates (`templates/admin_ontologies.html`, `templates/admin_missing_terms.html`)

### Cron

- `app/cron/ontologies.py` – periodic job that refreshes stale ontology indexes (respects index version history)

Acceptance: after indexing EDAM, `vector_search("sequence analysis")` returns relevant EDAM terms; the extraction agent uses them; missing terms are recorded with training-material links.

---

## 7. OpenAI-compatible API flexibility and model-selector agent

Make the LLM backend fully configurable and self-updating.

### LLM client (`app/agents/__init__.py`)

Currently three env-var tiers are used: `LLM_MODEL_SMALL`, `LLM_MODEL_LARGE`, `LLM_MODEL_EMBEDDING`
(defaults: `qwen2.5-coder-7b-instruct`, `gemma-3-27b-it`, `qwen3-embedding-8b`).
This issue replaces the three-tier approach with per-task model assignments stored in the DB:

- `get_llm_client(task)` looks up the preferred model for the given fine-grained task from the `model_assignments` table. Tasks: `content_relevance` (detect irrelevant JS/noise), `content_summary`, `link_decision`, `json_ld_review`, `metadata_analysis` (chain-of-thought reasoning scratchpad), `ontology_embedding`, `tool_discovery`, `model_selection`. Falls back to `LLM_MODEL_LARGE` env var.
- The `model_assignments` table includes a version history so previous assignments can be restored if a new selection is worse (e.g. a previously available model disappears).
- Try `response_format={"type":"json_schema",…}` for backends that support OpenAI structured outputs; fall back to `json_object` if unsupported.

### Model-selector agent (`app/agents/model_selector.py`)

- `ModelSelectorAgent` with `run(llm_client=None)`:
  - Lists all models available at the configured API endpoint
  - Optionally queries a public model-information source to gather capability metadata
  - Runs brief, timed capability probes for each task type and records latency alongside quality
  - Assigns the best available model to each task type
  - Updates the `model_assignments` table; previous assignment snapshot is kept for rollback
- A cron job (`app/cron/models.py`) runs `ModelSelectorAgent` periodically

### Admin UI (added to `app/admin/routes.py`)

- `GET /admin/models` – overview of current model assignments, latency data, and version history
- Admin can manually override any task → model assignment and add a free-text admin note per model/assignment (useful for flagging known issues)
- Rollback to a previous assignment snapshot
- Simple HTML template (`templates/admin_models.html`)

Acceptance: changing `OPENAI_API_BASE` to a different provider and restarting updates model assignments automatically on the next cron run; admin can see latency, override assignments, and roll back.

---

## 8. Admin interface refinements

Polish and unify the admin UI built incrementally across issues 5–7:

- Consistent navigation across all admin pages
- Admin dashboard at `GET /admin` summarising system status: last cron run times, pending sessions, ontology index ages, model assignment age
- Improve UX of existing admin pages (ontologies, tools, models, missing terms, sessions)
- Add pagination where needed
- Ensure all admin routes check `is_admin` flag

Acceptance: an admin user can navigate all admin pages from a single dashboard and see system health at a glance.

---

## 9. Structured agent logger

Replace the current string-based `log_fn` callback in `BioschemasExtractorAgent.run()` with a
structured logger that emits typed events:

- Define a typed event hierarchy: `InfoEvent`, `WarnEvent`, `LLMCallEvent` (task, model, prompt preview, response preview, latency ms), `FetchEvent` (url, status_code, content_length), `ItemFoundEvent`, `ValidationEvent`.
- The session viewer (`templates/sessions.html`) renders each event type with appropriate formatting and colour-coding: LLM calls show expandable prompts/responses, fetch events show HTTP status, validation events highlight errors.
- Keep backward compatibility by providing a `LegacyLogFn` adapter that wraps a plain `Callable[[str], None]` for the integration test runner.
- Add per-LLM-call timing statistics to the structured log so the integration test summary can report chunk classification counts, relevance rates, and total LLM call durations.

Acceptance: session viewer shows colour-coded event timeline; integration test summary includes per-phase timing statistics.
