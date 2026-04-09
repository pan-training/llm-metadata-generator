# llm-metadata-generator

An LLM-powered Flask service that generates [Bioschemas](https://bioschemas.org/) / [schema.org](https://schema.org/) JSON-LD metadata from training-material websites. Agents run on a cron schedule and results are stored in a SQLite database (with the [sqlite-vector](https://github.com/sqliteai/sqlite-vector/blob/main/packages/python/README.md) extension for ontology search).

---

## What it does

1. **Collection endpoint** – given a URL of a training-material collection, returns a Bioschemas JSON-LD list describing every course / learning resource found on that page.
2. **Single-resource endpoint** – given a URL of one training resource, returns a single Bioschemas JSON-LD object.
3. **Lazy generation** – the first request returns an empty (or previously cached) result while an agent generates fresh metadata in the background; the second request returns the result.
4. **Three update levels** – no change / incremental update / full refresh, chosen automatically based on how much the source website changed.
5. **Ontology support** – EDAM, PaNET and other ontologies are indexed in the vector database; the extraction agent uses nearest-neighbour search to find candidate terms quickly.
6. **Semantic-tool support** – bio.tools, FAIRsharing and similar search-interface sites get a dedicated discovery workflow that figures out how to query them, producing a reusable description for the main extraction agent.
7. **Flexible LLM backend** – any OpenAI-compatible API can be used; a dedicated agent periodically checks available models and updates which ones are used for each task.

---

## Architecture

```
llm-metadata-generator/
├── app/
│   ├── api/            # Flask blueprints (collection + single-resource endpoints)
│   ├── agents/         # LLM agent workflows (bioschemas, ontology, semantic-tool, model-selector)
│   ├── models/         # SQLAlchemy / raw-SQL models (User, Session, Metadata, …)
│   ├── db/             # SQLite + sqlite-vector initialisation helpers
│   ├── cron/           # APScheduler jobs (metadata refresh, ontology updates, tool updates)
│   └── admin/          # Admin blueprint (user management, ontology/tool admin interface)
├── templates/          # Jinja2 HTML templates (session viewer)
├── tests/              # pytest test suite
├── config.py           # Environment-driven configuration
├── requirements.txt    # Python dependencies
├── TODO.md             # Issue-ready todo list
└── .github/
    └── copilot-instructions.md
```

---

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/metadata?url=<url>[&prompt=<prompt>]` | Bioschemas JSON-LD **list** for a training collection |
| `GET` | `/metadata/single?url=<url>[&prompt=<prompt>]` | Bioschemas JSON-LD **object** for a single resource |

Both endpoints respond with `400` and a plain-text explanation when the target URL does not contain recognisable training content (events or learning materials).

Authentication is **Bearer token** (`Authorization: Bearer <token>`). Tokens are created by an admin CLI command.

---

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
flask db init          # initialise SQLite database
flask users create     # create the first admin user (prints token)
flask run
```

Set environment variables in a `.env` file (see `config.py` for all options):

```
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
DATABASE_URL=sqlite:///data/metadata.db
SECRET_KEY=change-me
```

---

## License

MIT – see [LICENSE](LICENSE).
