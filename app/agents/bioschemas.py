"""Bioschemas extraction agent.

## Extraction pipeline

The agent uses a four-phase pipeline designed to handle arbitrarily large
websites without overflowing the LLM context window.

### Phase 1 – CRAWL + DISCOVER  (tree-like, chunk-by-chunk)

1. Fetch the primary URL (respecting robots.txt).
2. Convert HTML to Markdown (via markdownify / BeautifulSoup4):
   - Scripts, styles, noscript, and template tags are stripped entirely.
   - Tables become standard Markdown tables, links become ``[anchor](url)``.
   - Heading hierarchy is preserved as ATX-style ``#`` headers.
3. Split the Markdown into overlapping chunks (CHUNK_SIZE chars, CHUNK_OVERLAP
   overlap) so context is never lost across chunk boundaries.
4. For each chunk, call a fast LLM (content_relevance task) to:
   a) decide if the chunk is relevant to training content;
   b) list any training items found in the chunk (title, URL, type);
   c) list any links worth following (with a short reason).
   All three outputs come from one LLM call per chunk.  A single chunk may
   contain multiple training items (e.g. a table row per item, or a prose
   paragraph describing two events), and the LLM is expected to return all
   of them in the ``items`` array.
5. Recursively follow the identified links up to MAX_FOLLOW_DEPTH, repeating
   steps 2–4 for each new page (up to MAX_TOTAL_PAGES total).

Result: a deduplicated list of DiscoveredItem objects, each carrying enough
context (source URL, surrounding text excerpt) to drive the extraction phase.

### Phase 2 – EXTRACT  (per item, separate context windows)

For each discovered item a two-step chain-of-thought extraction is performed
to improve accuracy, especially for smaller LLM models:

**Step 2a – Reasoning scratchpad** (free-text, no JSON mode):
The LLM reads the item's page content and writes concise notes about every
metadata field it can identify (type, title, dates, authors, topics, …).
This separates "what information is here?" from "format it as JSON-LD",
reducing the cognitive load on the model.

**Step 2b – Extraction** (JSON mode):
The model produces the Bioschemas JSON-LD object, with the scratchpad from
step 2a included as additional context in the prompt.

# TODO (issue #6): pass candidate ontology terms to the extraction prompt once
#   ontology vector search is implemented (see TODO.md item 6).

### Phase 3 – REVIEW  (per item)

Self-critical review: the LLM is asked to improve the extracted JSON-LD, check
for omissions, and verify field values against TeSS conventions.  The prompt
focuses on *content* quality — @context, @id, and dct:conformsTo are added
programmatically afterwards (not the model's concern).

### Phase 4 – VALIDATE + FIX

JSON schema validation against docs/Bioschemas/bioschemas-training-schema.json
(Draft 2020-12, via jsonschema).  TeSS programmatic conventions are applied
first (@context with dct namespace, dct:conformsTo, @id) so that the schema
can validate them.  Validation errors are then formatted as a concise list
and fed back to the LLM for up to MAX_FIX_ATTEMPTS fixing passes.

### Structural summary

After each successful crawl, a compact JSON summary is computed describing the
site's navigation structure (crawled URLs + content hashes, item count,
URL path patterns).  This is stored in metadata_cache.structural_summary and
passed back to the agent on subsequent runs so incremental updates know which
pages changed and which items are likely new.

### Update modes

- **Full refresh** (`structural_summary=None`): crawl everything from scratch.
- **Incremental** (`structural_summary` provided): the LLM is shown the
  previous summary and the page-hash map so it can skip unchanged content and
  focus on new or changed items.  Level-0 "no-op" is handled by the caller
  (compare content hashes before calling the agent).

This module must NOT import Flask.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from jsonschema import Draft202012Validator
from markdownify import markdownify as _md_convert

from app.agents import get_model_for_task
from app.agents.logger import AgentLogger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Chunk parameters for the HTML→text → LLM discovery pass.
CHUNK_SIZE = 4000   # characters per chunk sent to the LLM
CHUNK_OVERLAP = 400  # overlap between consecutive chunks (preserves context)

# Crawl limits.
MAX_TOTAL_PAGES = 20     # total pages fetched in one agent run
MAX_FOLLOW_DEPTH = 2     # maximum link-following depth from the primary URL
MAX_LINKS_PER_CHUNK = 10  # links the LLM may nominate per chunk

# Validation fix loop.
MAX_FIX_ATTEMPTS = 1  # maximum schema-fix LLM passes per item

# Maximum characters of item content sent to the extraction LLM.
MAX_EXTRACTION_CONTENT = 8000

_USER_AGENT = (
    "BioschemasMetadataGenerator/1.0 "
    "(+https://github.com/pan-training/llm-metadata-generator)"
)

# Path to the Bioschemas JSON schema (relative to this file).
_SCHEMA_PATH = (
    pathlib.Path(__file__).parent.parent.parent
    / "docs"
    / "Bioschemas"
    / "bioschemas-training-schema.json"
)

# ---------------------------------------------------------------------------
# Embedded system prompt  (used for extraction, review, and fix phases)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert metadata extractor for scientific training materials, courses,
and events — including life sciences, physical sciences, and related research
and education domains.
Your job is to extract high-quality Bioschemas/Schema.org JSON-LD metadata from
web-page text.

## Output a single JSON-LD object

### For TrainingMaterial / LearningResource
- name: full title (string)
- description: 2–5 sentence description (string)
- keywords: array of lowercase keyword strings

Strongly recommended:
- url: canonical URL
- author: [{"@type": "Person", "name": "...", "@id": "https://orcid.org/..."}]
  (include ORCID in @id whenever available)
- license: SPDX identifier (e.g. "CC-BY-4.0") or full CC URL
- inLanguage: IETF BCP 47 code (e.g. "en", "de", "fr")
- audience: [{"@type": "Audience", "audienceType": "beginner|intermediate|advanced"}]
- teaches: array of learning outcome strings
- educationalLevel: "beginner" | "intermediate" | "advanced"
- learningResourceType: array, e.g. ["tutorial", "video", "slides", "e-learning"]
- about: scientific topics as DefinedTerms
  (use EDAM URIs for life science: {"@type":"DefinedTerm","name":"Bioinformatics","url":"http://edamontology.org/topic_0091"}
   use PaNET for photon/neutron: {"@type":"DefinedTerm","name":"Tomography","url":"https://w3id.org/pan-training/PaNET01203"})
- timeRequired: ISO 8601 duration (e.g. "PT2H" = 2 hours, "P3D" = 3 days)
- identifier: DOI URL when present (e.g. "https://doi.org/10.1234/...")

### For CourseInstance (training event / workshop)
Required:
- @type: "CourseInstance"
- name: event title
- description: 2–5 sentence description
- courseMode: array — use exactly "online", "onsite", or "blended"
- location: for onsite: {"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"...","addressCountry":"..."}}
             for online: {"@type":"VirtualLocation","url":"..."}

Strongly recommended:
- startDate, endDate: ISO 8601 datetime ("2024-03-15T09:00:00" or "2024-03-15")
- url: canonical URL
- organizer: [{"@type": "Organization", "name": "..."}]
- maximumAttendeeCapacity: integer
- offers: [{"@type": "Offer", "price": 0, "priceCurrency": "EUR", "url": "..."}]

## TeSS ingestion conventions
- keywords: use an array, not a comma-separated string
- author/@id: use ORCID URI when known (TeSS extracts ORCID by regex)
- about: EDAM is primary for life science; PaNET for photon/neutron science
- identifier: include DOI as full URL; TeSS deduplicates on identifier
- inLanguage: language subtag only ("en", not "English")
- courseMode: TeSS maps "online" → virtual flag; use exact values listed above
- organizer vs provider: for events, prefer organizer; provider is the institution
"""

# ---------------------------------------------------------------------------
# Chunk classification system prompt  (simpler; not for JSON-LD extraction)
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM_PROMPT = """\
You are an expert at identifying scientific training content on web pages.
Your task is to classify a text chunk from a website and identify any training
materials, courses, or events it describes.  You do NOT produce JSON-LD here.
"""

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AccessDeniedError(Exception):
    """Raised when robots.txt or the server blocks crawling the primary URL."""


class NotTrainingContentError(Exception):
    """Raised when no training content is found after crawling."""


class MultipleTrainingContentError(Exception):
    """Raised in single-resource mode when multiple primary candidates exist."""


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


@dataclass
class DiscoveredItem:
    """A training item discovered during the crawl phase."""

    title: str
    url: str        # item's own URL if known, else the page where it was found
    item_type: str  # "TrainingMaterial" | "CourseInstance" | "Course"
    source_url: str  # page URL where this item was first mentioned
    context: str    # text excerpt surrounding the mention


@dataclass
class _CrawlState:
    """Mutable state accumulated during the crawl+discover phase."""

    # url → raw HTML text (stripped of scripts/styles)
    pages: dict[str, str] = field(default_factory=dict)
    # url → SHA-256 hash of the raw HTML
    page_hashes: dict[str, str] = field(default_factory=dict)
    # Deduplicated discovered items
    discovered: list[DiscoveredItem] = field(default_factory=list)
    # robots.txt cache: netloc → RobotFileParser (per-run cache)
    robots_cache: dict[str, urllib.robotparser.RobotFileParser] = field(
        default_factory=dict
    )


# ---------------------------------------------------------------------------
# HTML → Markdown with inline link annotations
# ---------------------------------------------------------------------------


def _html_to_markdown(
    html: str, base_url: str
) -> tuple[str, list[tuple[str, str]]]:
    """Convert HTML to Markdown and return absolute link list.

    Uses BeautifulSoup4 + markdownify to produce clean, structured Markdown:

    * ``<script>``, ``<style>``, ``<noscript>``, and ``<template>`` tags are
      **removed entirely** (content and tag) before conversion.
    * Tables become standard Markdown table syntax — each row is on its own
      line, preserving the column-per-attribute structure that many training
      catalogues use.  A single chunk may contain multiple training items,
      and the LLM is expected to return all of them.
    * Headings are preserved as ATX-style ``#`` headers.
    * Links appear as ``[anchor text](https://absolute-url)`` so the LLM
      always sees link context and destination together.
    * Images are kept as ``![alt](url)`` — TeSS displays images embedded in
      training-material descriptions.
    * Relative URLs are resolved to absolute using *base_url*.

    Returns:
        ``(markdown_text, [(absolute_url, anchor_text), ...])``
    """
    # Remove noise tags entirely (content stripped, not just the tag).
    soup = BeautifulSoup(html, "html.parser")
    for noise_tag in soup.find_all(
        ["script", "style", "noscript", "template"]
    ):
        noise_tag.decompose()

    # Convert to Markdown.
    md = _md_convert(
        str(soup),
        heading_style="ATX",
        bullets="-",
    )

    # Normalise excessive blank lines produced by block-level elements.
    md = re.sub(r"\n{3,}", "\n\n", md).strip()

    # Resolve relative link URLs to absolute and build the link list.
    links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    def _resolve_link(m: re.Match[str]) -> str:
        anchor = m.group(1)
        href = m.group(2).strip()
        if href.startswith(("#", "mailto:", "javascript:")):
            # Non-navigable — keep anchor text, drop the link syntax.
            return anchor
        absolute = urljoin(base_url, href)
        if urlparse(absolute).scheme not in ("http", "https"):
            return anchor
        if absolute not in seen_urls:
            seen_urls.add(absolute)
            links.append((absolute, anchor))
        return f"[{anchor}]({absolute})"

    md = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", _resolve_link, md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()

    return md, links


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------


def _chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """Split *text* into overlapping chunks, preferring paragraph/sentence breaks."""
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Prefer paragraph boundary
        para_break = text.rfind("\n\n", start, end)
        if para_break > start + chunk_size // 2:
            end = para_break
        else:
            # Fall back to sentence boundary
            for sep in (". ", ".\n", "? ", "! "):
                sent_break = text.rfind(sep, start, end)
                if sent_break > start + chunk_size // 2:
                    end = sent_break + len(sep)
                    break
        chunks.append(text[start:end])
        start = max(start + 1, end - overlap)
    return chunks


# ---------------------------------------------------------------------------
# robots.txt with per-run caching
# ---------------------------------------------------------------------------


def _check_robots(
    url: str,
    cache: dict[str, urllib.robotparser.RobotFileParser] | None = None,
) -> bool:
    """Return True if crawling *url* is allowed by robots.txt.

    *cache* is a per-crawl-session dict keyed by netloc; pass the same dict
    across all calls in one agent run to avoid re-fetching robots.txt for the
    same domain.
    """
    if cache is None:
        cache = {}
    parsed = urlparse(url)
    netloc = parsed.netloc
    if netloc not in cache:
        robots_url = f"{parsed.scheme}://{netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        try:
            rp.read()
        except Exception:
            # Treat unreachable robots.txt as "allow all"
            rp.allow_all = True  # type: ignore[attr-defined]
        cache[netloc] = rp
    return cache[netloc].can_fetch(_USER_AGENT, url)


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------


def _fetch(url: str) -> requests.Response:
    """GET *url* with the agent's User-Agent header, 15-second timeout."""
    return requests.get(
        url,
        headers={"User-Agent": _USER_AGENT},
        timeout=15,
    )


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------


def _call_llm(
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    logger: AgentLogger | None = None,
    task: str = "",
) -> dict[str, Any]:
    """Call the LLM chat completions API and parse the JSON response.

    Uses response_format={"type": "json_object"} which is supported by all
    major OpenAI-compatible backends (OpenAI, LocalAI, Ollama, …).
    # TODO (issue #7): try response_format={"type": "json_schema", ...} for
    #   backends that support structured outputs (better schema adherence).
    """
    prompt_text = "\n".join(m.get("content", "") for m in messages)
    t0 = time.monotonic()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
    )
    latency_ms = (time.monotonic() - t0) * 1000
    content = response.choices[0].message.content or "{}"
    if logger is not None:
        logger.llm_call(
            task=task,
            model=model,
            prompt=prompt_text,
            response=content,
            latency_ms=latency_ms,
        )
    try:
        return json.loads(content)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        # Try to salvage JSON if the model leaked extra text
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass
        return {}


def _call_llm_text(
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    logger: AgentLogger | None = None,
    task: str = "",
) -> str:
    """Call the LLM chat completions API and return the raw text response.

    Unlike :func:`_call_llm`, this function does **not** set
    ``response_format=json_object`` — it is intended for reasoning /
    chain-of-thought passes where free-form text is more appropriate than
    structured JSON output.
    """
    prompt_text = "\n".join(m.get("content", "") for m in messages)
    t0 = time.monotonic()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
    )
    latency_ms = (time.monotonic() - t0) * 1000
    content = (response.choices[0].message.content or "").strip()
    if logger is not None:
        logger.llm_call(
            task=task,
            model=model,
            prompt=prompt_text,
            response=content,
            latency_ms=latency_ms,
        )
    return content


# ---------------------------------------------------------------------------
# JSON schema validation (uses docs/Bioschemas/bioschemas-training-schema.json)
# ---------------------------------------------------------------------------

_SCHEMA: dict[str, Any] | None = None


def _get_schema() -> dict[str, Any]:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = json.loads(_SCHEMA_PATH.read_text())
    return _SCHEMA


def _validate_with_schema(item: dict[str, Any]) -> list[str]:
    """Validate *item* against the Bioschemas training JSON schema.

    Returns a list of human-readable error strings (empty = valid).
    The schema expects an array at the top level, so the item is wrapped.

    # TODO (issue #3 follow-up): surface richer schema descriptions to the LLM
    #   (e.g. include the "$comment" field of the failing property from the
    #   schema) to provide more actionable fix instructions.
    """
    try:
        schema = _get_schema()
        validator = Draft202012Validator(schema)
        errors = []
        for err in validator.iter_errors([item]):
            path = " → ".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append(f"{path}: {err.message}")
        return errors
    except Exception as exc:
        # Validation should never crash the pipeline
        return [f"Schema validation error: {exc}"]


# ---------------------------------------------------------------------------
# TeSS programmatic conventions  (applied after LLM output, not in the prompt)
# ---------------------------------------------------------------------------

_TESS_CONTEXT: dict[str, str] = {
    "@vocab": "https://schema.org/",
    "dct": "http://purl.org/dc/terms/",
}

_PROFILE_IRI: dict[str, str] = {
    "CourseInstance": "https://bioschemas.org/profiles/CourseInstance/1.0-RELEASE",
    "Course": "https://bioschemas.org/profiles/Course/1.0-RELEASE",
}
_DEFAULT_PROFILE_IRI = "https://bioschemas.org/profiles/TrainingMaterial/1.1-DRAFT"


def _apply_tess_conventions(item: dict[str, Any], fallback_url: str) -> dict[str, Any]:
    """Ensure TeSS-required structural fields are present.

    These are added programmatically so the LLM can focus on content.
    """
    # @context: always use the expanded form with dct namespace
    if not isinstance(item.get("@context"), dict):
        item["@context"] = _TESS_CONTEXT

    # dct:conformsTo: select profile IRI by @type
    if "dct:conformsTo" not in item:
        item_type = item.get("@type", "")
        profile = next(
            (iri for t, iri in _PROFILE_IRI.items() if t in item_type),
            _DEFAULT_PROFILE_IRI,
        )
        item["dct:conformsTo"] = {"@id": profile, "@type": "CreativeWork"}

    # @id: fall back to url field or the source URL
    if not item.get("@id"):
        item["@id"] = item.get("url") or fallback_url

    return item


# ---------------------------------------------------------------------------
# Structural summary  (stored in metadata_cache for incremental runs)
# ---------------------------------------------------------------------------


def compute_structural_summary(
    items: list[dict[str, Any]],
    source_url: str,
    crawled_page_hashes: dict[str, str] | None = None,
) -> str:
    """Produce a compact site-structure summary for future incremental runs.

    The summary describes *how to navigate* the site — not the content of each
    item (which is already stored in sessions.result_json).  On the next run
    the agent compares page hashes to identify changed pages and focuses only
    on those.
    """
    from datetime import datetime, timezone

    parsed = urlparse(source_url)
    item_urls = [
        i.get("url") or i.get("@id") or ""
        for i in items
        if i.get("url") or i.get("@id")
    ]

    return json.dumps(
        {
            "source_url": source_url,
            "source_domain": parsed.netloc,
            "last_extracted": datetime.now(timezone.utc).isoformat(),
            "item_count": len(items),
            "item_urls": item_urls[:100],  # cap to avoid huge payloads
            "crawled_page_hashes": crawled_page_hashes or {},
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    """Return a stable SHA-256 hex digest for the given text."""
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------


class BioschemasExtractorAgent:
    """Extracts Bioschemas JSON-LD from web pages about training materials.

    See the module docstring for a description of the four-phase pipeline.
    """

    def __init__(self) -> None:
        # Holds the AgentLogger for the duration of a run(); reset to None after.
        self._logger: AgentLogger | None = None

    def run(
        self,
        url: str,
        prompt: str | None = None,
        structural_summary: str | None = None,
        llm_client: Any = None,
        log_fn: Callable[[str], None] | None = None,
        logger: AgentLogger | None = None,
        on_item: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Extract Bioschemas JSON-LD from the given URL.

        Args:
            url: Primary source URL to crawl and extract from.
            prompt: Optional additional extraction instructions.
            structural_summary: JSON string produced by a previous run of
                ``compute_structural_summary``.  When provided, the agent runs
                in incremental mode and focuses on changed/new content.
                Pass ``None`` for a full refresh.
            llm_client: An OpenAI-compatible client instance (required).
            log_fn: Optional plain-string callback for backward compatibility.
                When *logger* is not provided, a new :class:`AgentLogger` is
                created with *log_fn* as its ``legacy_fn`` so that existing
                callers continue to receive progress messages unchanged.
            logger: Optional structured :class:`AgentLogger`.  Takes priority
                over *log_fn*.  When both are supplied, *log_fn* also receives
                ``info`` / ``warn`` message strings.
            on_item: Optional callback invoked with each fully processed
                JSON-LD item as it is produced (before the final list is
                returned).  Useful for streaming / partial result persistence.

        Returns:
            List of Bioschemas JSON-LD dicts.

        Raises:
            AccessDeniedError: Primary URL blocked by robots.txt or HTTP 401/403.
            NotTrainingContentError: No training content found after crawling.
        """
        if logger is None:
            self._logger = AgentLogger(legacy_fn=log_fn)
        else:
            # Both logger and log_fn provided – attach log_fn to the logger.
            if log_fn is not None and logger._legacy_fn is None:
                logger.set_legacy_fn(log_fn)
            self._logger = logger

        if llm_client is None:
            raise ValueError("llm_client must be provided")

        state = _CrawlState()

        # ----------------------------------------------------------------
        # Phase 1: CRAWL + DISCOVER
        # ----------------------------------------------------------------
        self._logger.info("Phase 1: crawl + discover")
        self._crawl_and_discover(
            start_url=url,
            structural_summary=structural_summary,
            llm_client=llm_client,
            state=state,
            is_primary=True,
        )

        if not state.discovered:
            raise NotTrainingContentError(
                f"No training content found at {url} (crawled "
                f"{len(state.pages)} page(s))"
            )

        self._logger.info(
            f"Discovery complete: {len(state.discovered)} item(s) found "
            f"across {len(state.pages)} page(s)"
        )

        # ----------------------------------------------------------------
        # Phase 2 + 3 + 4: EXTRACT, REVIEW, VALIDATE per item
        # ----------------------------------------------------------------
        final_items: list[dict[str, Any]] = []
        for item_info in state.discovered:
            self._logger.info(
                f"Extracting: {item_info.title!r} ({item_info.item_type})"
            )

            # Gather best available content for this item
            item_html = state.pages.get(
                item_info.url,
                state.pages.get(item_info.source_url, ""),
            )

            # If the item has its own URL and it's not yet crawled, fetch it
            if (
                item_info.url != url
                and item_info.url not in state.pages
                and _check_robots(item_info.url, state.robots_cache)
            ):
                try:
                    resp = _fetch(item_info.url)
                    self._logger.fetch(
                        url=item_info.url,
                        status_code=resp.status_code,
                        content_length=len(resp.text),
                    )
                    if resp.ok:
                        item_html = resp.text
                        self._logger.info(
                            f"  Fetched detail page: {item_info.url}"
                        )
                except requests.RequestException as exc:
                    self._logger.warn(f"Could not fetch detail page: {exc}")

            item_text, _ = _html_to_markdown(item_html or "", item_info.url or url)
            content_for_extraction = item_text[:MAX_EXTRACTION_CONTENT]

            # --- Chain-of-thought reasoning pass (step 2a) ---
            # The LLM writes free-text notes about what metadata it can
            # find before committing to a structured JSON format.  This
            # improves accuracy for smaller models by separating "what
            # information is here?" from "format it as JSON-LD".
            self._logger.info(f"  Reasoning about {item_info.title!r}")
            reasoning = self._reason_about_item(
                item_info=item_info,
                content=content_for_extraction,
                llm_client=llm_client,
            )

            # --- Extraction (step 2b) ---
            extracted = self._extract_item(
                item_info=item_info,
                content=content_for_extraction,
                prompt=prompt,
                reasoning=reasoning,
                llm_client=llm_client,
            )
            if not extracted:
                self._logger.warn("Skipping item — extraction returned empty result")
                continue

            # --- Review ---
            self._logger.info(
                f"  Reviewing: {extracted.get('name', 'unnamed')}"
            )
            reviewed = self._review_item(
                item=extracted,
                content=content_for_extraction,
                llm_client=llm_client,
            )

            # --- Validate + fix ---
            # Apply programmatic TeSS conventions first so the schema can
            # validate them (dct:conformsTo, @context, @id).
            reviewed = _apply_tess_conventions(reviewed, item_info.url or url)
            errors = _validate_with_schema(reviewed)
            item_name = reviewed.get("name", item_info.title)
            if not errors:
                self._logger.validation(
                    item_name=item_name, errors=[], passed=True
                )
            for attempt in range(MAX_FIX_ATTEMPTS):
                if not errors:
                    break
                self._logger.validation(
                    item_name=item_name, errors=errors, passed=False
                )
                self._logger.info(
                    f"  Validation errors ({len(errors)}); requesting fix from LLM"
                    f" (attempt {attempt + 1}/{MAX_FIX_ATTEMPTS})"
                )
                fixed = self._fix_item(
                    item=reviewed,
                    errors=errors,
                    llm_client=llm_client,
                )
                if fixed:
                    reviewed = _apply_tess_conventions(fixed, item_info.url or url)
                    errors = _validate_with_schema(reviewed)
                    if not errors:
                        self._logger.validation(
                            item_name=item_name, errors=[], passed=True
                        )

            final_items.append(reviewed)
            if on_item:
                on_item(reviewed)

        self._logger.info(f"Extraction complete: {len(final_items)} item(s)")
        return final_items

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _crawl_and_discover(
        self,
        start_url: str,
        structural_summary: str | None,
        llm_client: Any,
        state: _CrawlState,
        is_primary: bool = False,
        depth: int = 0,
    ) -> None:
        """Recursively crawl pages and populate *state.discovered*."""
        logger = self._logger
        if start_url in state.pages:
            return
        if len(state.pages) >= MAX_TOTAL_PAGES:
            if logger:
                logger.info(f"  Crawl limit ({MAX_TOTAL_PAGES} pages) reached")
            return

        # robots.txt check (raises for primary URL, logs and skips otherwise)
        if not _check_robots(start_url, state.robots_cache):
            if is_primary:
                raise AccessDeniedError(
                    f"Crawling blocked by robots.txt for {start_url}"
                )
            if logger:
                logger.warn(f"Skipping {start_url} (blocked by robots.txt)")
            return

        if logger:
            logger.info(
                f"  Fetching {'primary' if is_primary else f'depth-{depth}'} URL: {start_url}"
            )
        try:
            response = _fetch(start_url)
        except requests.RequestException as exc:
            if is_primary:
                raise AccessDeniedError(f"Failed to fetch {start_url}: {exc}") from exc
            if logger:
                logger.warn(f"Skipping {start_url}: {exc}")
            return

        if logger:
            logger.fetch(
                url=start_url,
                status_code=response.status_code,
                content_length=len(response.text),
            )

        if response.status_code in (401, 403):
            if is_primary:
                raise AccessDeniedError(
                    f"Access denied (HTTP {response.status_code}) for {start_url}"
                )
            if logger:
                logger.warn(
                    f"Skipping {start_url} (HTTP {response.status_code})"
                )
            return

        if not response.ok:
            msg = (
                f"Could not retrieve {start_url} "
                f"(HTTP {response.status_code} — the URL may be incorrect, "
                "the server may be temporarily unavailable, or access may be "
                "restricted)"
            )
            if is_primary:
                raise AccessDeniedError(msg)
            if logger:
                logger.warn(msg)
            return

        html = response.text
        state.pages[start_url] = html
        if logger:
            logger.info(
                f"  Page {len(state.pages)}/{MAX_TOTAL_PAGES}: "
                f"{len(html)} chars"
            )

        # Convert HTML → Markdown first; hash the stable Markdown content so
        # that cosmetic HTML changes (whitespace, inline styles, CDN URLs)
        # don't trigger unnecessary re-extraction.
        md_text, _ = _html_to_markdown(html, start_url)
        page_hash = _content_hash(md_text)
        state.page_hashes[start_url] = page_hash
        if logger:
            logger.info(f"  Markdown hash={page_hash[:12]}…")
        chunks = _chunk_text(md_text)
        total_chunks = len(chunks)
        if logger:
            logger.info(f"  Processing {total_chunks} chunk(s)")

        links_to_follow: list[str] = []

        for chunk_idx, chunk_text in enumerate(chunks):
            result = self._classify_chunk(
                chunk_text=chunk_text,
                chunk_index=chunk_idx,
                total_chunks=total_chunks,
                source_url=start_url,
                structural_summary=structural_summary if depth == 0 else None,
                llm_client=llm_client,
            )

            if not result.get("relevant", False):
                continue

            # Collect newly discovered items from this chunk
            new_items_in_chunk = 0
            for item_data in result.get("items", []):
                item_url = item_data.get("url", start_url)
                item_title = item_data.get("title", "")
                if not item_title:
                    continue
                # Deduplicate by (title, url)
                already_known = any(
                    d.title == item_title and d.url == item_url
                    for d in state.discovered
                )
                if not already_known:
                    item_type = item_data.get("item_type", "TrainingMaterial")
                    state.discovered.append(
                        DiscoveredItem(
                            title=item_title,
                            url=item_url,
                            item_type=item_type,
                            source_url=start_url,
                            context=item_data.get("context", ""),
                        )
                    )
                    if logger:
                        logger.item_found(
                            title=item_title,
                            url=item_url,
                            item_type=item_type,
                        )
                    new_items_in_chunk += 1

            if logger and new_items_in_chunk:
                logger.info(
                    f"  Chunk {chunk_idx + 1}/{total_chunks}: "
                    f"{new_items_in_chunk} new item(s) found (relevant)"
                )

            # Collect follow-links (deduplicated, capped)
            if depth < MAX_FOLLOW_DEPTH:
                for link_data in result.get("follow_links", [])[:MAX_LINKS_PER_CHUNK]:
                    link_url = link_data.get("url", "")
                    if (
                        link_url
                        and link_url not in state.pages
                        and link_url not in links_to_follow
                    ):
                        links_to_follow.append(link_url)

        # Recursively follow identified links
        for follow_url in links_to_follow:
            self._crawl_and_discover(
                start_url=follow_url,
                structural_summary=None,  # only used at depth 0
                llm_client=llm_client,
                state=state,
                depth=depth + 1,
            )

    def _classify_chunk(
        self,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        source_url: str,
        structural_summary: str | None,
        llm_client: Any,
    ) -> dict[str, Any]:
        """Ask the LLM to classify a text chunk and extract items + links."""
        incremental_note = ""
        if structural_summary:
            try:
                summary = json.loads(structural_summary)
                prev_count = summary.get("item_count", "unknown")
                item_urls = summary.get("item_urls", [])
                incremental_note = (
                    f"\nPrevious crawl found {prev_count} item(s). "
                    f"Focus on items NOT in this list: {item_urls[:20]}\n"
                )
            except (json.JSONDecodeError, AttributeError):
                pass

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Analyse this text chunk ({chunk_index + 1}/{total_chunks}) "
                    f"from {source_url}.{incremental_note}\n\n"
                    "Identify:\n"
                    "1. Training materials, courses, or events mentioned\n"
                    "2. Links worth following to find more training content\n\n"
                    "Output JSON:\n"
                    '{"relevant": true/false, "items": [{"title": "...", '
                    '"url": "...", "item_type": "TrainingMaterial|CourseInstance|Course", '
                    '"context": "excerpt mentioning this item"}], '
                    '"follow_links": [{"url": "...", "reason": "..."}]}\n\n'
                    f"Text chunk:\n{chunk_text}"
                ),
            },
        ]
        return _call_llm(
            llm_client,
            get_model_for_task("content_relevance"),
            messages,
            logger=self._logger,
            task="content_relevance",
        )

    def _reason_about_item(
        self,
        item_info: DiscoveredItem,
        content: str,
        llm_client: Any,
    ) -> str:
        """Produce a chain-of-thought reasoning scratchpad for a single item.

        The LLM is asked to read the page content and write concise free-text
        notes about every metadata field it can identify — **without** producing
        JSON.  This separates "what information is here?" from "format it as
        JSON-LD", reducing cognitive load and improving accuracy for smaller
        models.

        The resulting notes are passed as additional context to the subsequent
        :meth:`_extract_item` call (step 2b of the extraction phase).
        """
        messages: list[dict[str, str]] = [
            {
                "role": "user",
                "content": (
                    "You are about to extract Bioschemas JSON-LD metadata for "
                    "a training item. First, carefully read the page content "
                    "below and write brief notes on what metadata you can find. "
                    "Cover:\n"
                    "- Type (LearningResource for training material / tutorial, "
                    "CourseInstance for scheduled event / workshop)\n"
                    "- Title (exact wording from the page)\n"
                    "- Description (key points in 2–5 sentences)\n"
                    "- Authors / instructors (and ORCIDs if visible)\n"
                    "- Dates (start/end; note if absent)\n"
                    "- Location or mode (online / onsite / blended)\n"
                    "- Scientific topics / keywords\n"
                    "- Educational level and target audience\n"
                    "- License\n"
                    "- Language\n"
                    "- Any other fields evident in the content\n\n"
                    "Write concise notes in plain text. "
                    "Do NOT produce JSON yet.\n\n"
                    f"Item: {item_info.title} ({item_info.item_type})\n"
                    f"URL: {item_info.url}\n"
                    f"Context hint: {item_info.context}\n\n"
                    f"Page content:\n{content}"
                ),
            },
        ]
        return _call_llm_text(
            llm_client,
            get_model_for_task("metadata_analysis"),
            messages,
            logger=self._logger,
            task="metadata_analysis",
        )

    def _extract_item(
        self,
        item_info: DiscoveredItem,
        content: str,
        prompt: str | None,
        reasoning: str | None,
        llm_client: Any,
    ) -> dict[str, Any]:
        """Extract Bioschemas JSON-LD for a single discovered item.

        *reasoning* is the chain-of-thought scratchpad produced by
        :meth:`_reason_about_item` (step 2a).  When provided it is included
        in the prompt as additional context so the model does not need to
        re-analyse the page from scratch.
        """
        extra = f"\nAdditional instructions: {prompt}\n" if prompt else ""
        reasoning_section = (
            f"\n\nYour prior analysis of this item:\n{reasoning}\n"
            if reasoning
            else ""
        )

        # TODO (issue #6): insert candidate ontology terms here once ontology
        #   vector search is implemented (see TODO.md item 6).

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Extract Bioschemas JSON-LD for this training item."
                    f"{reasoning_section}{extra}\n\n"
                    f"Title: {item_info.title}\n"
                    f"URL: {item_info.url}\n"
                    f"Type: {item_info.item_type}\n"
                    f"Context: {item_info.context}\n\n"
                    "Output a single valid Bioschemas JSON-LD object.\n\n"
                    f"Page content:\n{content}"
                ),
            },
        ]
        return _call_llm(
            llm_client,
            get_model_for_task("json_ld_review"),
            messages,
            logger=self._logger,
            task="json_ld_extraction",
        )

    def _review_item(
        self,
        item: dict[str, Any],
        content: str,
        llm_client: Any,
    ) -> dict[str, Any]:
        """Self-critical review pass; returns improved JSON-LD."""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Critically review the following Bioschemas JSON-LD and improve it.\n"
                    "Focus on: completeness of metadata fields, correct @type, accurate "
                    "keywords array, valid courseMode values, ORCID in author/@id when "
                    "available, ISO 8601 dates.\n"
                    "Return the complete improved JSON-LD object.\n\n"
                    f"Source content (excerpt):\n{content[:2000]}\n\n"
                    f"Current JSON-LD:\n{json.dumps(item, indent=2)}"
                ),
            },
        ]
        result = _call_llm(
            llm_client,
            get_model_for_task("json_ld_review"),
            messages,
            logger=self._logger,
            task="json_ld_review",
        )
        return result if result else item

    def _fix_item(
        self,
        item: dict[str, Any],
        errors: list[str],
        llm_client: Any,
    ) -> dict[str, Any]:
        """Fix schema validation errors via a targeted LLM call."""
        error_list = "\n".join(f"- {e}" for e in errors[:20])
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "The following Bioschemas JSON-LD has validation errors. "
                    "Fix ALL of them and return the corrected JSON-LD object.\n\n"
                    f"Validation errors:\n{error_list}\n\n"
                    f"Current JSON-LD:\n{json.dumps(item, indent=2)}"
                ),
            },
        ]
        result = _call_llm(
            llm_client,
            get_model_for_task("json_ld_review"),
            messages,
            logger=self._logger,
            task="json_ld_fix",
        )
        return result if result else item

