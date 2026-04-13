# Copilot Instructions for llm-metadata-generator

> **Self-update instruction:** Whenever the repository structure changes significantly (new directories, renamed modules, new endpoints, new agents, new cron jobs), update this file to reflect the current state. You can ask Copilot: _"Update `.github/copilot-instructions.md` to reflect the current state of the codebase."_
>
> Also check `README.md` after any API endpoint change and update it to match.

---

## Repository layout

Files are marked **📋 planned** (not yet created) or **✅ exists**.

```
llm-metadata-generator/
├── app/
│   ├── __init__.py          ✅  Flask application factory (create_app)
│   ├── api/
│   │   ├── __init__.py      ✅
│   │   ├── _extraction.py   ✅  Shared background extraction job
│   │   ├── collection.py    ✅  GET /metadata  – returns JSON-LD list for a training collection
│   │   ├── resource.py      ✅  GET /metadata/single – returns single JSON-LD object
│   │   └── sessions.py      ✅  GET /sessions + POST /sessions/login (session viewer)
│   ├── agents/
│   │   ├── __init__.py      ✅  get_llm_client() + get_model_for_task() (env-var model config)
│   │   ├── bioschemas.py    ✅  Main extraction agent: chunk-based crawl, integrated link-follow, JSON schema validation
│   │   ├── ontology.py      📋  Ontology indexing agent (EDAM, PaNET, …)
│   │   ├── semantic_tool.py 📋  Semantic-tool discovery agent (bio.tools, FAIRsharing, …)
│   │   └── model_selector.py📋  Agent that checks available OpenAI-compatible models
│   ├── models/
│   │   ├── __init__.py      ✅
│   │   ├── user.py          ✅  User model – Bearer token auth, no username/password
│   │   ├── session.py       ✅  Session model – tracks generation state per (user, url)
│   │   └── metadata.py      📋  Cached Bioschemas metadata per URL
│   ├── db/
│   │   ├── __init__.py      ✅
│   │   ├── sqlite.py        ✅  SQLite init + sqlite-vector extension loading
│   │   └── schema.sql       ✅  Plain-SQL CREATE TABLE statements (all schema migrations live here)
│   ├── cron/
│   │   ├── __init__.py      ✅
│   │   ├── metadata.py      ✅  Cron: trigger metadata refresh for tracked URLs
│   │   ├── ontologies.py    📋  Cron: keep ontology vector index up to date
│   │   └── tools.py         📋  Cron: refresh semantic-tool descriptions
│   └── admin/
│       ├── __init__.py      📋
│       └── routes.py        📋  Admin blueprint: user CLI, ontology/tool admin UI
├── docs/
│   └── Bioschemas/
│       ├── bioschemas-training-schema.json  ✅  JSON Schema (Draft 2020-12) for validation
│       └── examples/        ✅  Example JSON-LD files used in tests
├── templates/
│   └── sessions.html        ✅  HTML session viewer (login via POST /sessions/login)
├── tests/
│   ├── __init__.py          ✅
│   ├── test_app.py          ✅  Smoke tests: app factory, db init
│   ├── test_api.py          ✅  API endpoint tests
│   ├── test_agents.py       ✅  Agent tests with mock LLM client
│   ├── test_auth.py         ✅  Authentication tests
│   └── test_bioschemas_examples.py  ✅  Schema validation tests for example JSON-LD files
├── config.py                ✅  All config read from environment variables
├── pyproject.toml           ✅  Poetry project + dependency definitions
├── .env.example             ✅  Placeholder values for all environment variables
├── TODO.md                  ✅  Ordered issue-ready todo list (✅ = done, no marker = future)
├── .gitignore               ✅
├── LICENSE                  ✅
└── .github/
    ├── workflows/
    │   └── ci.yml           ✅  GitHub Actions: pytest + mypy on every push
    └── copilot-instructions.md   ✅  ← this file
```
│   └── test_auth.py         📋
├── config.py                ✅  All config read from environment variables
├── pyproject.toml           ✅  Poetry project + dependency definitions
├── .env.example             ✅  Placeholder values for all environment variables
├── TODO.md                  ✅  Ordered issue-ready todo list
├── .gitignore               ✅
├── LICENSE                  ✅
└── .github/
    ├── workflows/
    │   └── ci.yml           ✅  GitHub Actions: pytest + mypy on every push
    └── copilot-instructions.md   ✅  ← this file
```

**Schema migrations** will live in `app/db/schema.sql` as plain `CREATE TABLE IF NOT EXISTS` statements. There is no migration framework (no Alembic). When the schema changes, update `schema.sql` and re-run `flask db init` (which re-applies the file idempotently).

> ⚠️ **Introduce Alembic at the first migration.** The plain-SQL approach only supports adding new tables and nullable columns. As soon as any destructive migration is needed (renaming a column, moving data, removing a column, etc.) **switch to Alembic immediately** — do not accumulate ad-hoc migration scripts. Introduce it early; retrofitting Alembic onto a live database with many ad-hoc changes is painful.

---

## Key conventions

> **Convention update policy 📋/✅:** Conventions are marked 📋 (planned, not yet coded) or ✅ (implemented and followed throughout the codebase). Changing a **✅** convention is allowed but **requires refactoring all existing code** that follows it before the convention marker is updated. Never leave the codebase in a mixed state. Before updating a marker from 📋 to ✅ or changing a ✅ convention, use `grep -r` (or equivalent) to confirm every usage site has been updated and that existing tests still pass.

- 📋 **Flask application factory** – `create_app(config=None)` lives in `app/__init__.py`. Tests pass a test config dict; production reads from `config.py`.
- 📋 **Blueprints** – each sub-package under `app/` that serves HTTP routes registers its own `Blueprint` and is registered in `create_app`.
- 📋 **Authentication** – every API request must carry `Authorization: Bearer <token>`. The `@require_token` decorator (defined in `app/models/user.py`) validates the token against the database. There are no usernames or passwords. Tokens must **never** be passed as a URL query parameter in GET requests (security risk); browser-facing pages use a POST `/sessions/login` endpoint that sets a session cookie instead.
- 📋 **Admin CLI** – Flask CLI commands (registered via `@app.cli.command`) under the `users` group handle token creation. Example: `flask users create`.
- ✅ **Database** – SQLite file path comes from `DATABASE_URL` env var (default: `data/metadata.db`). All schema migrations are plain SQL in `app/db/schema.sql` (no Alembic). **Introduce Alembic at the first migration that goes beyond adding a table or nullable column** (i.e. rename, data move, column drop). The sqlite-vector extension is loaded at connection time in `app/db/sqlite.py` **only after the ontology feature is implemented** (see TODO item 6).
- 📋 **Agents** – agents are plain Python classes with a `run(**kwargs)` method. They accept an `llm_client` argument so they can be tested with a mock. Agent code must never import Flask directly.
- ✅ **Cron / background jobs** – APScheduler (background scheduler) is started inside `create_app`. Each cron module exposes a `register(scheduler)` function that adds its jobs. APScheduler jobs have no Flask request context; always push the app context manually: `with app.app_context(): ...`. Use `get_db()` inside that context just as in a normal request.
- 📋 **Generation flow** – on first call for a URL the API returns the current cached result (empty list if none) and enqueues a background generation task. On subsequent calls the latest completed result is returned and a new generation is enqueued. The session model tracks state.
- 📋 **Update levels** – determined by comparing a hash of the fetched web content against the stored hash:
  - **Level 0 – No update:** hash matches stored hash → skip entirely.
  - **Level 1 – Incremental:** hash changed → agent receives a structural summary of the last crawl and focuses on changed/new items.
  - **Level 2 – Full refresh:** triggered (a) randomly with a very low probability (e.g. ~1 % of cron runs) to catch drift, (b) when the agent itself reports that the site structure has fundamentally changed since the structural summary was created, or (c) when no stored hash exists. The `force_refresh` query parameter exists for admin/debugging use only — computer agents never set it.
- 📋 **Ontology search** – the bioschemas agent calls `app.db.sqlite.vector_search(query, top_k)` to find candidate ontology terms. This capability is added to the agent in the same issue that implements ontology indexing (TODO item 6).
- 📋 **Semantic tools** – tools (bio.tools, FAIRsharing, …) are **globally admin-managed** (not per-user). A short one-line description of every configured tool is always included in the extraction agent's system prompt so it is aware of available tools. When the agent decides to use a specific tool, it requests the full detailed description on demand. Tool descriptions are stored in the `semantic_tools` table and refreshed by a cron job.
- 📋 **LLM configuration** – all LLM calls go through `app/agents/__init__.py:get_llm_client(task)`, which reads `OPENAI_API_BASE`, `OPENAI_API_KEY`, and looks up the preferred model for the given task from the `model_assignments` table. Tasks are fine-grained: `content_relevance` (detect irrelevant JS/noise), `content_summary`, `link_decision`, `json_ld_review`, `ontology_embedding`, `tool_discovery`, `model_selection`.
- 📋 **Bioschemas / TeSS** – the extraction agent's system prompt includes the Bioschemas TrainingMaterial and CourseInstance profiles and notes about TeSS-specific field usage. Keep this prompt in `app/agents/bioschemas.py`, not in a separate template file.
- ✅ **File-system paths** – always use `pathlib.Path` for path construction and file I/O; do not use `os.path` or raw string concatenation for paths.
- ✅ **Modern Python idioms** – prefer built-in modern equivalents over manual workarounds: use `str.removeprefix` / `str.removesuffix` instead of slicing, `X | Y` union types instead of `Optional[X]`, walrus operator where it aids clarity, etc.
- ✅ **Type annotations** – add type annotations to all public functions and methods. Run `mypy app tests` before finalising any commit to ensure no type errors are introduced.
- ✅ **TODO.md** – whenever code introduces a `# TODO:` placeholder comment, it must be accompanied by a concrete item in `TODO.md` (added to an existing future issue or a new issue). Mark completed issues in `TODO.md` with **✅ Done** so it is clear which items are future work. When a new `# TODO:` comment is added in code that does not map to an existing future issue, create a new issue entry in `TODO.md` with sufficient detail to become a GitHub issue.

---

## Environment variables (see `config.py` for defaults)

| Variable | Purpose |
|---|---|
| `OPENAI_API_BASE` | Base URL of the OpenAI-compatible API |
| `OPENAI_API_KEY` | API key |
| `DATABASE_URL` | SQLite file path (default: `data/metadata.db`) |
| `SECRET_KEY` | Flask secret key |
| `CRON_METADATA_INTERVAL` | **Minutes** between metadata refresh runs (default 1440 = daily) |
| `CRON_ONTOLOGY_INTERVAL` | **Hours** between ontology index refreshes (default 720 = monthly) |
| `CRON_TOOLS_INTERVAL` | **Hours** between semantic-tool description refreshes (default 168 = weekly) |
| `LLM_MODEL_SMALL` | Fast model for classification/routing tasks (default: `qwen2.5-coder-7b-instruct`) |
| `LLM_MODEL_LARGE` | Quality model for extraction/review tasks (default: `gemma-3-27b-it`) |
| `LLM_MODEL_EMBEDDING` | Embedding model for ontology vector search — TODO #6 (default: `qwen3-embedding-8b`) |

---

## Running tests

```bash
pytest tests/
```

## Type checking

```bash
mypy app tests
```

Run this before finalising any commit.

---

## Adding a new agent

1. Create `app/agents/<name>.py` with a class that inherits nothing special – just needs `run(**kwargs)`.
2. Add a test in `tests/test_agents.py`.
3. If it requires a new cron job, add it in `app/cron/` and register it in `create_app`.
4. Update this file and `README.md` to reflect the new module.
