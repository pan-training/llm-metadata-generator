# Integration Tests

Run the Bioschemas extraction agent against real websites to evaluate its output quality, identify edge cases, and guide further development.

## Running integration tests

> **Prerequisites:** a running LLM backend with `OPENAI_API_BASE` and `OPENAI_API_KEY` configured in `.env`.

```bash
# Run all sites listed in config.json
flask integration-test run

# Run a single site from the config
flask integration-test run --url https://training.galaxyproject.org/training-material/

# Run an ad-hoc URL not in the config
flask integration-test run --url https://example.com/training/

# Override the extraction prompt for all sites
flask integration-test run --prompt "Focus on hands-on workshops only"

# Use a custom config file or output directory
flask integration-test run --config /path/to/my-sites.json --output-dir /tmp/results
```

## Output format

Each run creates a subdirectory inside `integration_test/results/`:

```
integration_test/results/<sanitized-domain>__<timestamp>/
├── config.json            ← inputs used: URL, prompt, model settings
├── log.txt                ← agent log messages (one per line)
├── result.json            ← extracted Bioschemas JSON-LD items (array)
├── structural_summary.json← site-structure summary for future incremental runs
└── summary.md             ← human-readable overview: item count, validation status, errors
```

The `result.json` file is an array of Bioschemas JSON-LD objects.  Each object also contains a top-level `_validation` key with any JSON-schema errors detected so issues are immediately visible.

## Sharing results

Commit the results you want to share:

```bash
git add integration_test/results/
git commit -m "integration test results: <site>"
git push
```

This lets collaborators examine the raw output and identify where the extraction approach can be improved.

## Modifying the site list

Edit `integration_test/config.json`.  Each entry has:

| Field | Required | Description |
|---|---|---|
| `url` | ✅ | Primary URL to crawl |
| `description` | | Human-readable label (shown in output) |
| `prompt` | | Extra extraction instructions for this site |
| `notes` | | Internal notes about what this site tests |

## Adding new sites

Add new entries to `config.json` for sites that exercise specific scenarios:
- **Large catalogues** — test pagination / crawl depth
- **Event-heavy sites** — test `CourseInstance` extraction and date handling
- **Single-resource pages** — test single `LearningResource` extraction
- **Non-English sites** — test `inLanguage` extraction
- **Sites with ORCID authors** — test author / ORCID extraction
