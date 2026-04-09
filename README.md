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

## API

> **Note:** These endpoints are planned and not yet implemented. See the issue list for implementation status.
> When new endpoints are added or changed, update this section accordingly.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/metadata?url=<url>[&prompt=<prompt>]` | Bioschemas JSON-LD **list** for a training collection |
| `GET` | `/metadata/single?url=<url>[&prompt=<prompt>]` | Bioschemas JSON-LD **object** for a single resource |

Both endpoints respond with `400` and a plain-text explanation when the target URL does not contain recognisable training content (events or learning materials).

<details>
<summary>Authentication</summary>

All API requests must include a Bearer token in the `Authorization` header:

```
Authorization: Bearer <token>
```

Tokens are created by an admin CLI command (`flask users create`) – there are no usernames or passwords. The HTML session viewer uses a login page where you POST your token to get a browser session.

</details>

---

## Setup

```bash
# Install dependencies (using Poetry)
poetry install

# Initialise the database
flask db init

# Create the first admin user (prints token)
flask users create

# Start the development server
flask run
```

Copy `.env.example` to `.env` and fill in your values (see `config.py` for all options):

```
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
SECRET_KEY=change-me
```

> **Tip:** The SQLite database is stored in `data/` by default. Advanced configuration is documented in `config.py`.

---

## Where to start

- New API endpoints → `app/api/`
- LLM agent workflows → `app/agents/`
- Database models and schema → `app/models/` and `app/db/schema.sql`
- Background cron jobs → `app/cron/`
- Admin interface → `app/admin/`

---

## License

MIT – see [LICENSE](LICENSE).
