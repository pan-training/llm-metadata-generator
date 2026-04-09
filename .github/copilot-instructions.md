# Copilot Instructions for llm-metadata-generator

> **Self-update instruction:** Whenever the repository structure changes significantly (new directories, renamed modules, new endpoints, new agents, new cron jobs), update this file to reflect the current state. You can ask Copilot: _"Update `.github/copilot-instructions.md` to reflect the current state of the codebase."_
>
> Also verify that `README.md` is still accurate after significant changes — update the API table, the "Where to start" links, and the setup instructions if needed.

---

## Repository layout

Status icons: 🟢 = file/directory already exists · 🔲 = planned (not yet created)

```
llm-metadata-generator/
├── 🟢 app/
│   ├── 🔲 __init__.py          # Flask application factory (create_app)
│   ├── 🔲 api/
│   │   ├── 🔲 __init__.py
│   │   ├── 🔲 collection.py    # GET /metadata  – returns JSON-LD list for a training collection
│   │   └── 🔲 resource.py      # GET /metadata/single – returns single JSON-LD object
│   ├── 🔲 agents/
│   │   ├── 🔲 __init__.py      # get_llm_client(task) helper
│   │   ├── 🔲 bioschemas.py    # Main extraction agent: reads web, follows links, validates JSON-LD
│   │   ├── 🔲 ontology.py      # Ontology indexing agent (EDAM, PaNET, …)
│   │   ├── 🔲 semantic_tool.py # Semantic-tool discovery agent (bio.tools, FAIRsharing, …)
│   │   └── 🔲 model_selector.py# Agent that checks available OpenAI-compatible models
│   ├── 🔲 models/
│   │   ├── 🔲 __init__.py
│   │   ├── 🔲 user.py          # User model – Bearer token auth, no username/password
│   │   ├── 🔲 session.py       # Session model – tracks generation state per (user, url)
│   │   └── 🔲 metadata.py      # Cached Bioschemas metadata per URL
│   ├── 🔲 db/
│   │   ├── 🔲 __init__.py
│   │   ├── 🔲 sqlite.py        # SQLite init + sqlite-vector extension loading
│   │   └── 🔲 schema.sql       # CREATE TABLE statements (plain SQL, no Alembic)
│   ├── 🔲 migrations/          # Plain SQL migration scripts (e.g. 001_add_column.sql)
│   ├── 🔲 cron/
│   │   ├── 🔲 __init__.py
│   │   ├── 🔲 metadata.py      # Cron: trigger metadata refresh for tracked URLs
│   │   ├── 🔲 ontologies.py    # Cron: keep ontology vector index up to date
│   │   ├── 🔲 tools.py         # Cron: refresh semantic-tool descriptions
│   │   └── 🔲 models.py        # Cron: run model-selector agent
│   └── 🔲 admin/
│       ├── 🔲 __init__.py
│       └── 🔲 routes.py        # Admin blueprint: user CLI, ontology/tool admin UI
├── 🔲 templates/
│   ├── 🔲 sessions.html        # HTML session viewer
│   └── 🔲 admin_*.html         # Admin UI templates
├── 🔲 tests/
│   ├── 🔲 __init__.py
│   ├── 🔲 test_api.py
│   ├── 🔲 test_agents.py
│   └── 🔲 test_auth.py
├── 🔲 config.py                # All config read from environment variables
├── 🔲 pyproject.toml           # Poetry project file and dependencies
├── 🔲 .env.example             # Example environment variables
├── 🟢 .gitignore
├── 🟢 LICENSE
└── 🟢 .github/
    └── 🟢 copilot-instructions.md   # ← this file
```

---

## Key conventions

> **Convention update policy:** Conventions listed here can be updated as the project evolves, but any update requires refactoring all existing code that follows the old convention before the change is merged. Mark updated conventions with `[updated YYYY-MM-DD]` until the refactor is confirmed complete.
>
> Status icons (🟢 implemented · 🔲 planned) are also used here to make convention changes easier to track.

- 🔲 **Flask application factory** – `create_app(config=None)` lives in `app/__init__.py`. Tests pass a test config dict; production reads from `config.py`.
- 🔲 **Blueprints** – each sub-package under `app/` that serves HTTP routes registers its own `Blueprint` and is registered in `create_app`.
- 🔲 **Authentication** – every API request must carry `Authorization: Bearer <token>`. The `@require_token` decorator (defined in `app/models/user.py`) validates the token against the database. There are no usernames or passwords. **Never pass tokens as GET query parameters** – the session viewer login uses a POST form that establishes a browser session.
- 🔲 **Admin CLI** – Flask CLI commands (registered via `@app.cli.command`) under the `users` group handle token creation. Example: `flask users create`.
- 🔲 **Dependency management** – use Poetry (`pyproject.toml`). Do not use a bare `requirements.txt`.
- 🔲 **Database** – SQLite file path comes from `DATABASE_URL` env var (default: `data/metadata.db`). All schema definitions live in `app/db/schema.sql`; incremental changes go in `app/migrations/` as plain SQL scripts (no Alembic).
- 🔲 **Agents** – agents are plain Python classes with a `run(**kwargs)` method. They accept an `llm_client` argument so they can be tested with a mock. Agent code must never import Flask directly.
- 🔲 **Cron** – APScheduler (background scheduler) is started inside `create_app`. Each cron module exposes a `register(scheduler)` function that adds its jobs.
- 🔲 **Generation flow** – on first call for a URL the API returns the current cached result (empty list if none) and enqueues a background generation task. On subsequent calls the latest completed result is returned and a new generation is enqueued. The session model tracks state.
- 🔲 **Update levels** – determined by comparing a hash of the fetched web content against the stored hash:
  - *Level 0 – Skip:* hash unchanged → no LLM call.
  - *Level 1 – Incremental:* hash changed → agent receives a summary of the website structure and a diff-hint; focuses only on new/changed items. Also triggered when the agent detects the site structure has changed significantly since the last summary was generated.
  - *Level 2 – Full refresh:* missing hash, or a rare random roll (very infrequently, to guard against accumulated drift) → full re-crawl from scratch. `force_refresh` is **not** a query parameter (API callers are typically agents that would never pass it); instead, full refresh is triggered programmatically or by the random roll.
- 🔲 **Ontology search** – the bioschemas agent calls `app.db.sqlite.vector_search(query_embedding, top_k)` to find candidate ontology terms before filling in ontology fields. This capability is only available after the ontology indexing issue is completed; earlier issues should note it as a future capability.
- 🔲 **Semantic tools** – tool descriptions are stored in the `semantic_tools` table (admin-managed, not per-user). Each tool has a short summary and a full description. The bioschemas agent always includes the **short summary** of every tool in its system prompt; if the agent decides to use a tool it fetches the **full description** on demand.
- 🔲 **LLM configuration** – all LLM calls go through `app/agents/__init__.py:get_llm_client(task)`, which reads `OPENAI_API_BASE`, `OPENAI_API_KEY`, and the current model mapping from the database (populated by the model-selector agent). Task names are fine-grained: `"page_filtering"`, `"summarization"`, `"link_selection"`, `"extraction"`, `"jsonld_review"`, `"embedding"`.
- 🔲 **Bioschemas / TeSS** – the extraction agent's system prompt includes the Bioschemas TrainingMaterial and CourseInstance profiles and notes about TeSS-specific field usage. Keep this prompt in `app/agents/bioschemas.py`, not in a separate template file.

---

## Environment variables (see `config.py` for defaults)

| Variable | Purpose |
|---|---|
| `OPENAI_API_BASE` | Base URL of the OpenAI-compatible API |
| `OPENAI_API_KEY` | API key |
| `DATABASE_URL` | SQLite file path (default: `sqlite:///data/metadata.db`) |
| `SECRET_KEY` | Flask secret key |
| `CRON_METADATA_INTERVAL` | Minutes between metadata refresh runs (default 60) |
| `CRON_ONTOLOGY_INTERVAL` | Hours between ontology index refreshes (default 24) |
| `CRON_TOOLS_INTERVAL` | Hours between semantic-tool description refreshes (default 12) |
| `CRON_MODELS_INTERVAL` | Hours between model-selector runs (default 24) |

---

## Running tests

```bash
pytest tests/
```

---

## Adding a new agent

1. Create `app/agents/<name>.py` with a class that inherits nothing special – just needs `run(**kwargs)`.
2. Add a test in `tests/test_agents.py`.
3. If it requires a new cron job, add it in `app/cron/` and register it in `create_app`.
4. Update this file to reflect the new module (status icons, layout, conventions).
