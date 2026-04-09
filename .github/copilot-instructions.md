# Copilot Instructions for llm-metadata-generator

> **Self-update instruction:** Whenever the repository structure changes significantly (new directories, renamed modules, new endpoints, new agents, new cron jobs), update this file to reflect the current state. You can ask Copilot: _"Update `.github/copilot-instructions.md` to reflect the current state of the codebase."_

---

## Repository layout

```
llm-metadata-generator/
├── app/
│   ├── __init__.py          # Flask application factory (create_app)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── collection.py    # GET /metadata  – returns JSON-LD list for a training collection
│   │   └── resource.py      # GET /metadata/single – returns single JSON-LD object
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── bioschemas.py    # Main extraction agent: reads web, follows links, validates JSON-LD
│   │   ├── ontology.py      # Ontology indexing agent (EDAM, PaNET, …)
│   │   ├── semantic_tool.py # Semantic-tool discovery agent (bio.tools, FAIRsharing, …)
│   │   └── model_selector.py# Agent that checks available OpenAI-compatible models
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py          # User model – Bearer token auth, no username/password
│   │   ├── session.py       # Session model – tracks generation state per (user, url)
│   │   └── metadata.py      # Cached Bioschemas metadata per URL
│   ├── db/
│   │   ├── __init__.py
│   │   └── sqlite.py        # SQLite init + sqlite-vector extension loading
│   ├── cron/
│   │   ├── __init__.py
│   │   ├── metadata.py      # Cron: trigger metadata refresh for tracked URLs
│   │   ├── ontologies.py    # Cron: keep ontology vector index up to date
│   │   └── tools.py         # Cron: refresh semantic-tool descriptions per user
│   └── admin/
│       ├── __init__.py
│       └── routes.py        # Admin blueprint: user CLI, ontology/tool admin UI
├── templates/
│   └── sessions.html        # HTML session viewer (login with Bearer token)
├── tests/
│   ├── __init__.py
│   ├── test_api.py
│   ├── test_agents.py
│   └── test_auth.py
├── config.py                # All config read from environment variables
├── requirements.txt
├── TODO.md                  # Ordered issue-ready todo list
├── .gitignore
├── LICENSE
└── .github/
    └── copilot-instructions.md   # ← this file
```

---

## Key conventions

- **Flask application factory** – `create_app(config=None)` lives in `app/__init__.py`. Tests pass a test config dict; production reads from `config.py`.
- **Blueprints** – each sub-package under `app/` that serves HTTP routes registers its own `Blueprint` and is registered in `create_app`.
- **Authentication** – every API request must carry `Authorization: Bearer <token>`. The `@require_token` decorator (defined in `app/models/user.py`) validates the token against the database. There are no usernames or passwords.
- **Admin CLI** – Flask CLI commands (registered via `@app.cli.command`) under the `users` group handle token creation. Example: `flask users create`.
- **Database** – SQLite file path comes from `DATABASE_URL` env var. The sqlite-vector extension is loaded at connection time in `app/db/sqlite.py`. All schema migrations are plain SQL scripts (no Alembic).
- **Agents** – agents are plain Python classes with a `run(**kwargs)` method. They accept an `llm_client` argument so they can be tested with a mock. Agent code must never import Flask directly.
- **Cron** – APScheduler (background scheduler) is started inside `create_app`. Each cron module exposes a `register(scheduler)` function that adds its jobs.
- **Generation flow** – on first call for a URL the API returns the current cached result (empty list if none) and enqueues a background generation task. On subsequent calls the latest completed result is returned and a new generation is enqueued. The session model tracks state.
- **Update levels** – determined by comparing a hash of the fetched web content against the stored hash: identical → skip; changed → incremental update; `force_refresh` query param or missing hash → full refresh.
- **Ontology search** – the bioschemas agent calls `app.db.sqlite.vector_search(query, top_k)` to find candidate ontology terms before filling in ontology fields.
- **Semantic tools** – per-user tool descriptions are stored in the `semantic_tools` table. The discovery agent writes a plain-text description; the bioschemas agent receives this description in its system prompt.
- **LLM configuration** – all LLM calls go through `app/agents/__init__.py:get_llm_client()`, which reads `OPENAI_API_BASE`, `OPENAI_API_KEY`, and the current model mapping from the database (populated by the model-selector agent).
- **Bioschemas / TeSS** – the extraction agent's system prompt includes the Bioschemas TrainingMaterial and CourseInstance profiles and notes about TeSS-specific field usage. Keep this prompt in `app/agents/bioschemas.py`, not in a separate template file.

---

## Environment variables (see `config.py` for defaults)

| Variable | Purpose |
|---|---|
| `OPENAI_API_BASE` | Base URL of the OpenAI-compatible API |
| `OPENAI_API_KEY` | API key |
| `DATABASE_URL` | SQLite file path, e.g. `sqlite:///data/metadata.db` |
| `SECRET_KEY` | Flask secret key |
| `CRON_METADATA_INTERVAL` | Minutes between metadata refresh runs (default 60) |
| `CRON_ONTOLOGY_INTERVAL` | Hours between ontology index refreshes (default 24) |
| `CRON_TOOLS_INTERVAL` | Hours between semantic-tool description refreshes (default 12) |

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
4. Update this file to reflect the new module.
