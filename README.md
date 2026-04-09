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

> **Note for contributors:** When adding or changing API endpoints, update this README and `.github/copilot-instructions.md` to reflect the current state.

---

## API

> ⚠️ The endpoints below are planned but not yet implemented. See [`TODO.md`](TODO.md) for the implementation roadmap.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/metadata?url=<url>[&prompt=<prompt>]` | Bioschemas JSON-LD **list** for a training collection |
| `GET` | `/metadata/single?url=<url>[&prompt=<prompt>]` | Bioschemas JSON-LD **object** for a single resource |

<details>
<summary>Error responses</summary>

| Status | When |
|--------|------|
| `400` | The target URL does not contain recognisable training content (events or learning materials) |
| `403` | The target URL is blocked by `robots.txt` or returned an access-denied response |
| `401` | Missing or invalid Bearer token |

All error responses include a plain-text body explaining the reason.

</details>

Authentication uses **Bearer tokens** (`Authorization: Bearer <token>`). Tokens are created by an admin CLI command — there are no usernames or passwords.

---

## Setup

```bash
# Install dependencies (requires Python ≥ 3.11 and Poetry)
poetry install

# Initialise the SQLite database
flask db init

# Create the first admin user (prints the Bearer token)
flask users create

# Start the development server
flask run
```

Set the required environment variables in a `.env` file (see `config.py` for all available options):

```
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
SECRET_KEY=change-me
```

The database is stored in `data/metadata.db` by default. Override with `DATABASE_URL` in `.env` if needed.

---

## Where to start

New to the codebase? Start with these files:

- `app/__init__.py` – application factory (`create_app`)
- `app/agents/bioschemas.py` – core extraction agent
- `app/api/` – HTTP endpoints
- `.github/copilot-instructions.md` – full architecture conventions

---

## License

MIT – see [LICENSE](LICENSE).
