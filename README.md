# llm-metadata-generator

An LLM-powered Flask service that generates [Bioschemas](https://bioschemas.org/) / [schema.org](https://schema.org/) JSON-LD metadata from training-material websites. Agents run on a cron schedule and results are stored in a SQLite database (with the [sqlite-vector](https://github.com/sqliteai/sqlite-vector/blob/main/packages/python/README.md) extension for ontology search).

---

## What it does

1. **Collection endpoint** – given a URL of a training-material collection, returns a Bioschemas JSON-LD list describing every course / learning resource found on that page.
2. **Single-resource endpoint** – given a URL of one training resource, returns a single Bioschemas JSON-LD object.
3. **Lazy generation** – the first request returns an empty (or previously cached) result while an agent generates fresh metadata in the background; the second request returns the result.
4. **Smart updates** – incremental refresh (only changed pages re-crawled) vs. full refresh, chosen automatically based on stored page hashes.
5. **Ontology support** *(planned, issue #6)* – EDAM, PaNET and other ontologies will be indexed in the vector database; the extraction agent uses nearest-neighbour search to find candidate terms.
6. **Semantic-tool support** *(planned, issue #5)* – bio.tools, FAIRsharing and similar sites get a dedicated discovery workflow.
7. **Flexible LLM backend** – any OpenAI-compatible API can be used; model selection is driven by three env-var tiers (`LLM_MODEL_SMALL`, `LLM_MODEL_LARGE`, `LLM_MODEL_EMBEDDING`) with defaults suited to common open-source deployments.

> **Note for contributors:** When adding or changing API endpoints, update this README and `.github/copilot-instructions.md` to reflect the current state.

---

## API

All endpoints require `Authorization: Bearer <token>`.

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

## Session viewer

Browse extraction results and agent logs in a browser without needing `curl` or an API client:

1. Navigate to `http://localhost:5000/sessions`
2. Log in with your Bearer token (POST form — token never appears in the URL)
3. See a colour-coded table of sessions with expandable JSON-LD and full agent logs

---

## Extraction agent architecture

The extraction agent (`app/agents/bioschemas.py`) uses a four-phase pipeline designed to handle arbitrarily large websites without overflowing the LLM context window.

### Phase 1 – Crawl + Discover  *(chunk-by-chunk, tree-like)*

1. Fetch the primary URL (respecting `robots.txt`).
2. Strip JavaScript, CSS, and navigation noise → clean text with inline link annotations  
   (`anchor text [→ https://target.url]`).
3. Split the text into overlapping chunks (4000 chars, 400-char overlap) so context is never lost across boundaries.
4. For **each chunk**, one fast LLM call (`content_relevance` task / small model) simultaneously:
   - Decides whether the chunk is relevant to training content.
   - Lists any training items found (title, URL, type, surrounding text excerpt).
   - Lists any links worth following (with a short reason).
5. Follow identified links recursively up to `MAX_FOLLOW_DEPTH` (default: 2), up to `MAX_TOTAL_PAGES` (default: 20) pages total.

This produces a deduplicated list of `DiscoveredItem` objects, each with enough context to drive the extraction phase.

### Phase 2 – Extract  *(per item, separate context windows)*

For each discovered item:
- Fetch the item's own URL if it differs from the source page and is not yet crawled.
- Strip and chunk the item page.
- Call the quality LLM (`json_ld_review` task / large model) to produce a full Bioschemas JSON-LD object.

### Phase 3 – Review  *(per item)*

Self-critical pass: the LLM is asked to improve the extracted JSON-LD, checking field completeness and TeSS conventions.  Technical boilerplate (`@context`, `@id`, `dct:conformsTo`) is *not* the model's concern — it is added programmatically afterwards.

### Phase 4 – Validate + Fix

- Validate against `docs/Bioschemas/bioschemas-training-schema.json` (Draft 2020-12, via `jsonschema`).
- If errors exist, format them as a concise list and pass back to the LLM for a single fixing pass.
- Apply programmatic TeSS conventions: `@context` with `dct` namespace, `dct:conformsTo` with the correct profile IRI, `@id` fallback.

### Structural summary

After each successful crawl, a compact JSON summary is stored in `metadata_cache.structural_summary` containing:
- `crawled_page_hashes` — SHA-256 hashes of every crawled page.
- `item_url_common_prefix` — common path prefix of all item URLs (navigation hint).
- `item_count`, `item_urls` — for detecting new/removed items on the next run.

On the next run the agent compares page hashes to skip unchanged pages and focuses on new or changed items (incremental mode).  Pass `structural_summary=None` to force a full refresh.

---

## Setup

### Using Poetry (recommended)

```bash
# Requires Python ≥ 3.11 and Poetry ≥ 2.0
poetry install

# Initialise the SQLite database
flask db init

# Create the first admin user (prints the Bearer token)
flask users create

# Start the development server
flask run
```

> **Poetry version note:** If your Poetry installation is older than 2.0 you may encounter schema errors. Upgrade Poetry with `pip install --upgrade poetry` or use the pip-based setup below.

### Without Poetry (pip)

If you cannot use Poetry, install the runtime dependencies with pip:

```bash
pip install flask apscheduler requests "openai>=2.0" python-dotenv jsonschema markdownify beautifulsoup4
flask db init
flask users create
flask run
```

> Prefer Poetry — it ensures exact dependency versions as tested in CI.

### Environment variables

Copy `.env.example` to `.env` and fill in the required values:

```dotenv
OPENAI_API_BASE=https://your-openai-compatible-api/v1
OPENAI_API_KEY=your-key
SECRET_KEY=change-me-to-a-random-string

# Model defaults (common open-source models on LocalAI-compatible backends)
LLM_MODEL_SMALL=qwen2.5-coder-7b-instruct   # fast: classification, link decisions
LLM_MODEL_LARGE=gemma-3-27b-it              # quality: extraction, review
LLM_MODEL_EMBEDDING=qwen3-embedding-8b      # embeddings: ontology search (TODO #6)
```

See `config.py` for all available options and their defaults.

The database is stored in `data/metadata.db` by default. Override with `DATABASE_URL` in `.env` if needed.

---

## Running tests

```bash
poetry run pytest tests/ -v
poetry run mypy app tests
```

---

## Integration tests

Run the extraction agent against real websites to evaluate output quality:

```bash
# Run all pre-configured sites
flask integration-test run

# Single ad-hoc URL
flask integration-test run --url https://example.com/training/

# Limit how long each site may take (skip to next site after 10 minutes)
flask integration-test run --timeout 600
```

Results are saved to `integration_test/results/<site>__<timestamp>/` with a live-updating `log.txt` and incrementally-written `result.json` (partial results visible even if the run is interrupted).  See [`integration_test/README.md`](integration_test/README.md) for full documentation.

---

## Where to start

New to the codebase? Start with these files:

- `app/__init__.py` – application factory (`create_app`)
- `app/agents/bioschemas.py` – core extraction agent (module docstring explains the full pipeline)
- `app/api/` – HTTP endpoints
- `.github/copilot-instructions.md` – full architecture conventions
- `TODO.md` – implementation roadmap (✅ = done)

---

## License

MIT – see [LICENSE](LICENSE).
