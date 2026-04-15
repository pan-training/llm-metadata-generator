"""Bioschemas extraction agent.

## Extraction pipeline

The agent uses a multi-phase pipeline designed to handle arbitrarily large
websites without overflowing the LLM context window.

### Phase 0 – STRUCTURAL SUMMARY  (computed once, reused across runs)

Before extraction the caller computes a rich structural summary via
``compute_site_structure_summary``.  This is an expensive but reusable
operation: it crawls several structural/navigation pages, produces a brief
LLM summary of each, and then compiles a final JSON document that describes:

- A short description of the website's purpose.
- The primary training content types found on the site.
- For each content type: the URL where it lives, navigation patterns
  (pagination, category links), 2–4 concrete examples, and a note on how
  items are typically structured on the page.

The structural summary is cached in ``metadata_cache.structural_summary``.
Phase 0 is **skipped** on subsequent runs when a cached summary exists.

### Phase 1 – CRAWL + DISCOVER  (tree-like, chunk-by-chunk)

When a structural summary (new ``schema_version=2`` format) is available the
agent **starts from the ``primary_url`` fields** listed under each content
type rather than from the root URL.  For each start URL:

1. Fetch the page (respecting robots.txt).
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
   When a structural summary is available the chunk prompt includes the site
   description and examples so the LLM focuses only on the primary training
   content types described in the summary and ignores secondary content.
5. Recursively follow the identified links up to MAX_FOLLOW_DEPTH, repeating
   steps 2–4 for each new page (up to MAX_TOTAL_PAGES total).

Result: a deduplicated list of DiscoveredItem objects, each carrying enough
context (source URL, surrounding text excerpt) to drive the extraction phase.

### Phase 2 – EXTRACT  (per item, separate context windows)

For each discovered item the page content is split into extraction chunks.
A fast relevance model first marks which chunks contain metadata evidence for
that specific item.  Then a two-step chain-of-thought extraction is performed
per relevant chunk to improve accuracy, especially for smaller LLM models:

**Step 2a – Reasoning scratchpad** (free-text, no JSON mode):
The LLM reads the item's page content and writes concise notes about every
metadata field it can identify (type, title, dates, authors, topics, …).
This separates "what information is here?" from "format it as JSON-LD",
reducing the cognitive load on the model.

**Step 2b – Extraction** (JSON mode):
The model produces the Bioschemas JSON-LD object, with the scratchpad from
step 2a included as additional context in the prompt.

The chunk-level extraction outputs are finally merged into one JSON-LD object.

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

### Structural summary format (schema_version=2)

The new structural summary produced by ``compute_site_structure_summary`` is a
JSON object with the following structure::

    {
        "schema_version": "2",
        "source_url": "https://example.com",
        "source_domain": "example.com",
        "computed_at": "2024-01-01T00:00:00+00:00",
        "site_description": "A website hosting bioinformatics tutorials …",
        "content_types": [
            {
                "type": "TrainingMaterial",
                "description": "Online tutorials for bioinformatics tools",
                "primary_url": "https://example.com/training",
                "navigation": {
                    "type": "paginated",
                    "urls": ["https://example.com/training?page=2"],
                    "description": "URL pattern ?page=N; 'Next' link present"
                },
                "examples": [
                    {
                        "title": "Intro to Python",
                        "description": "Beginner Python tutorial",
                        "url": "https://example.com/training/python"
                    }
                ],
                "typical_structure": "Title, difficulty badge, topic tags, Start button"
            }
        ]
    }

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
from urllib.parse import urljoin, urlparse, parse_qs

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

# Structural summary computation limits (Phase 0).
MAX_STRUCTURE_PAGES = 8    # max pages to crawl when building a structural summary
# Characters of page content used per page for structure analysis (beginning+end).
STRUCTURE_CONTENT_SIZE = 3000

# Validation fix loop.
MAX_FIX_ATTEMPTS = 1  # maximum schema-fix LLM passes per item

# Maximum characters of item content sent to the extraction LLM.
MAX_EXTRACTION_CONTENT = 8000
# Characters per extraction chunk when long pages are split for item-level extraction.
EXTRACTION_CHUNK_SIZE = 2500
# Overlap between extraction chunks to avoid losing boundary context.
EXTRACTION_CHUNK_OVERLAP = 250
# Cap of cached/seeded item detail URLs used to bootstrap incremental runs.
MAX_INCREMENTAL_START_URLS = 200

# Maximum characters of a schema property description included in validation
# error hints (keeps the hint concise for LLM context).
MAX_SCHEMA_HINT_LENGTH = 120

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

## STRICT RULES — read carefully
- ONLY include information that is EXPLICITLY present in the provided page content.
- NEVER invent, guess, or hallucinate any field value.
- NEVER include an ORCID unless the full ORCID URL (https://orcid.org/XXXX-XXXX-XXXX-XXXX)
  is visibly present in the page content for that specific author.
- NEVER include a DOI unless a full DOI URL (https://doi.org/...) is explicitly
  present in the page content.
- NEVER include a license unless it is explicitly stated in the page content.
- NEVER include an ontology term URL (EDAM, PaNET, etc.) unless you can confirm the
  exact term name and URI from the page content — do not construct or guess URIs.
- The training platform or aggregator website (e.g. "PaN-Training", "TeSS", "ELIXIR")
  is NOT an author. Only list actual individuals or contributing organisations as authors.
- Do NOT include the collection/listing URL (e.g. /materials, /events) as the item's
  url — use the item's own detail page URL.

## Output a single JSON-LD object

### For TrainingMaterial / LearningResource
- name: full title (string)
- description: 2–5 sentence description (string)
- keywords: array of lowercase keyword strings

Strongly recommended (only when found in the page):
- url: canonical URL of the item's own detail page
- author: [{"@type": "Person", "name": "..."}]  add "@id": "https://orcid.org/..." only if ORCID is explicitly on the page
- license: SPDX identifier (e.g. "CC-BY-4.0") or full CC URL — ONLY if stated on the page
- inLanguage: IETF BCP 47 code (e.g. "en", "de", "fr")
- audience: [{"@type": "Audience", "audienceType": "beginner|intermediate|advanced"}]
- teaches: array of learning outcome strings
- educationalLevel: "beginner" | "intermediate" | "advanced"
- learningResourceType: array, e.g. ["tutorial", "video", "slides", "e-learning"]
- about: scientific topics as DefinedTerms — ONLY use EDAM or PaNET URIs you recognise exactly
  (EDAM example: {"@type":"DefinedTerm","name":"Bioinformatics","url":"http://edamontology.org/topic_0091"}
   PaNET example: {"@type":"DefinedTerm","name":"Tomography","url":"https://w3id.org/pan-training/PaNET01203"})
  If uncertain about the URI, omit the "url" field and just include the name.
- timeRequired: ISO 8601 duration (e.g. "PT2H" = 2 hours, "P3D" = 3 days)
- identifier: DOI URL — ONLY if explicitly present on the page

### For CourseInstance (training event / workshop)
Required:
- @type: "CourseInstance"
- name: event title
- description: 2–5 sentence description
- courseMode: array — use exactly "online", "onsite", or "blended"
- location: for onsite: {"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"...","addressCountry":"..."}}
             for online: {"@type":"VirtualLocation","url":"..."}

Strongly recommended (only when found in the page):
- startDate, endDate: ISO 8601 datetime ("2024-03-15T09:00:00" or "2024-03-15")
- url: canonical URL
- organizer: [{"@type": "Organization", "name": "..."}]
- maximumAttendeeCapacity: integer
- offers: [{"@type": "Offer", "price": 0, "priceCurrency": "EUR", "url": "..."}]

## TeSS ingestion conventions
- keywords: use an array, not a comma-separated string
- author/@id: use ORCID URI ONLY when the full ORCID URL is visible on the page
- about: EDAM is primary for life science; PaNET for photon/neutron science
- identifier: include DOI as full URL; TeSS deduplicates on identifier
- inLanguage: language subtag only ("en", not "English")
- courseMode: TeSS maps "online" → virtual flag; use exact values listed above
- organizer vs provider: for events, prefer organizer; provider is the institution
  hosting the content permanently (not the event organiser)
"""

# ---------------------------------------------------------------------------
# Chunk classification system prompt  (simpler; not for JSON-LD extraction)
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM_PROMPT = """\
You are an expert at identifying scientific training content on web pages.
Your task is to classify a text chunk from a website and identify any training
materials, courses, or events it describes.  You do NOT produce JSON-LD here.

IMPORTANT — follow ONLY the navigation pattern from the structural summary:
When a structural summary is provided, it describes exactly how to navigate the
site (e.g. "paginated with ?page=N" or "category pages at /topics/X").
In follow_links, include ONLY links that match that described navigation pattern.
Do NOT follow other links even if they look interesting.

IMPORTANT — faceted search / filter interfaces:
Many training catalogues have filter panels (checkboxes, dropdowns, tag clouds,
sort controls) that narrow or re-order the *same* list of items without adding
new content.  These produce URLs like:
  ?category=bioinformatics  ?sort=date  ?type=online  ?tag=python  ?level=beginner
These are NOT worth following — they just limit or re-sort the result set.
Filter/tag option labels are also NOT training items, even when they look like
topic names (e.g. "DALIA", "FAIR", "Open Educational Resources").
Never extract these controls as items.
Only include a link in follow_links if it leads to a DIFFERENT page of content
(e.g. a next-page pagination link with ?page=N, or a genuinely different content
section), NOT if it merely filters, re-sorts, or modifies the current list.

IMPORTANT — relevance for navigation-only chunks:
If a chunk only contains navigation/filter UI, tag clouds, sort controls, auth
links, cookie/privacy text, or other scaffolding (and no concrete item card/row/
detail content), set relevant=false and return an empty items array.

IMPORTANT — skip non-content pages:
Do NOT include links to creation, editing, admin, or login pages in
follow_links.  Examples to skip: /new, /create, /edit, /delete, /admin,
/sign_in, /login, /register.
"""

# ---------------------------------------------------------------------------
# System prompts for structural summary computation (Phase 0)
# ---------------------------------------------------------------------------

_STRUCTURE_PAGE_SYSTEM_PROMPT = """\
You are an expert at analysing training content websites.
Analyse the provided web page content and describe its structure in JSON.
Focus on: what type of page this is, what training content is visible,
and how to navigate to more content (pagination, categories, etc.).
"""

_STRUCTURE_COMPILE_SYSTEM_PROMPT = """\
You are an expert at analysing training content websites.
You have been provided with summaries of several pages from a training website.
Produce a rich structural summary that will guide a metadata extraction agent.

IMPORTANT — content type selection:
- By default ONLY include "TrainingMaterial" and "CourseInstance" content types.
- DO NOT include Workflows, LearningPaths, Spaces, or other non-standard content
  types UNLESS the user explicitly provided a URL that points directly to that
  type of content (e.g. /workflows or /learning_paths was the given entry URL).
- The simplest, most direct interpretation of the site is preferred.

IMPORTANT — navigation URLs:
- In each content type's navigation.urls, include ONLY URLs that lead to
  additional pages of the SAME content type (e.g. pagination URLs like ?page=2,
  ?page=3, or category landing pages).
- DO NOT include URLs with filter/facet query parameters (e.g. ?sort=date,
  ?type=online, ?category=..., etc.) unless those parameters were already
  present in the user-provided entry URL.
- The navigation description MUST clearly state the URL pattern to follow
  (e.g. "Append ?page=N (N=2,3,…) to the primary URL; 'Next' link below list").

IMPORTANT — examples:
- Include 2–4 concrete example items from the page summaries.
- Keep examples short — they are inserted into every LLM prompt during extraction.
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
    # url → hash from the previous run (from structural summary)
    previous_page_hashes: dict[str, str] = field(default_factory=dict)
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
        escape_misc=False,
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


def _clean_html_for_llm(
    html: str, base_url: str
) -> tuple[str, list[tuple[str, str]]]:
    """Strip noise tags and resolve URLs in *html* without converting to Markdown.

    Produces cleaned HTML suitable for passing directly to an LLM when the
    caller wants the model to see raw HTML structure instead of Markdown.
    The same noise tags removed by :func:`_html_to_markdown` (``script``,
    ``style``, ``noscript``, ``template``) are stripped here too so the
    output remains compact.  Relative URLs in ``<a href>`` and ``<img src>``
    attributes are resolved to absolute using *base_url*.

    Returns:
        ``(cleaned_html, [(absolute_url, anchor_text), ...])``
    """
    soup = BeautifulSoup(html, "html.parser")
    for noise_tag in soup.find_all(["script", "style", "noscript", "template"]):
        noise_tag.decompose()

    links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href = str(a_tag["href"]).strip()
        if href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        a_tag["href"] = absolute
        anchor = a_tag.get_text(strip=True)
        if absolute not in seen_urls:
            seen_urls.add(absolute)
            links.append((absolute, anchor))

    for img_tag in soup.find_all("img", src=True):
        src = str(img_tag["src"]).strip()
        if not src.startswith(("data:", "#")):
            img_tag["src"] = urljoin(base_url, src)

    return str(soup), links


def _page_content(
    html: str, base_url: str, raw_html: bool
) -> tuple[str, list[tuple[str, str]]]:
    """Return ``(text, links)`` using either Markdown or cleaned HTML.

    When *raw_html* is ``False`` (default) the result of
    :func:`_html_to_markdown` is returned.  When *raw_html* is ``True`` the
    cleaned HTML from :func:`_clean_html_for_llm` is returned instead so the
    LLM sees the original HTML structure.  Either way, the same set of
    absolute links is extracted and returned.
    """
    if raw_html:
        return _clean_html_for_llm(html, base_url)
    return _html_to_markdown(html, base_url)


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


def _join_chunks_with_limit(
    chunks: list[str],
    max_chars: int,
    separator: str = "\n\n---\n\n",
) -> str:
    """Join complete chunks without exceeding *max_chars*."""
    if not chunks:
        return ""

    joined_parts: list[str] = []
    used_chars = 0
    for chunk in chunks:
        sep_len = len(separator) if joined_parts else 0
        if used_chars + sep_len + len(chunk) > max_chars:
            break
        if joined_parts:
            joined_parts.append(separator)
            used_chars += len(separator)
        joined_parts.append(chunk)
        used_chars += len(chunk)

    if joined_parts:
        return "".join(joined_parts)
    return chunks[0][:max_chars]


# ---------------------------------------------------------------------------
# Faceted search URL detection
# ---------------------------------------------------------------------------

# Query-string parameter names that typically represent facet filters / sort
# controls on a search/listing page.  Links that only differ from the current
# page by these parameters are just narrowing the same result set and should
# NOT be followed as new content pages.
_FILTER_PARAMS: frozenset[str] = frozenset(
    {
        # Sorting / ordering
        "sort", "sort_by", "order", "order_by", "orderby", "direction",
        # Category / type / format filters
        "category", "cat", "type", "format", "topic", "subject", "theme",
        # Tag / keyword filters
        "tag", "tags", "keyword", "keywords",
        # Audience / level
        "audience", "level", "difficulty", "target",
        # Language / locale
        "language", "lang", "locale",
        # Status / date filters
        "status", "date", "from", "to", "year", "month",
        # Free-text search within the page
        "q", "query", "search", "s",
        # View style (grid vs list, etc.)
        "view", "display",
    }
)

# Parameters that indicate genuine pagination — these should still be followed.
_PAGINATION_PARAMS: frozenset[str] = frozenset(
    {"page", "p", "pg", "offset", "start", "skip", "cursor", "after", "before"}
)

# URL path segments that identify non-content pages (admin, auth, CRUD).
# Only block pages that definitely cannot contain training content.
# Note: "search" and "api" are intentionally excluded because search pages
# and API endpoints can sometimes yield useful training-content metadata.
_NON_CONTENT_PATH_SEGMENTS: frozenset[str] = frozenset(
    {
        "new", "create", "edit", "update", "delete", "destroy",
        "admin", "sign_in", "sign_up", "login", "logout", "register",
        "password", "account", "profile", "settings",
    }
)


def _is_non_content_url(url: str) -> bool:
    """Return True when *url* is a non-content page (admin, auth, CRUD, API).

    These URLs are never worth following for training content extraction.
    The check looks at every segment of the URL path.
    """
    path = urlparse(url).path.lower()
    segments = {seg for seg in path.split("/") if seg}
    return bool(segments & _NON_CONTENT_PATH_SEGMENTS)


def _is_faceted_search_url(url: str, source_url: str) -> bool:
    """Return True when *url* appears to be a faceted-search / filter variation.

    A URL is considered a faceted-search variant when both of the following
    hold:

    1. The URL path is identical to *source_url*'s path (same listing page).
    2. The query string consists **entirely** of known filter/sort parameters
       (``_FILTER_PARAMS``) — none of the query parameters are pagination
       parameters (``_PAGINATION_PARAMS``) or path-changing parameters not in
       the filter list.

    Pagination links (``?page=2``, ``?offset=20``) are explicitly allowed
    because they genuinely navigate to *different* slices of content.

    Examples that return True (should NOT follow):
      ``https://example.com/courses?sort=date``
      ``https://example.com/courses?category=bioinformatics&sort=title``
      ``https://example.com/events?format=online&level=beginner``

    Examples that return False (safe to follow):
      ``https://example.com/courses?page=2``          # pagination
      ``https://example.com/courses/python``           # different path
      ``https://example.com/courses?category=bio&page=2``  # pagination + filter
    """
    parsed_url = urlparse(url)
    parsed_src = urlparse(source_url)

    # Different path → this is a distinct page, not a faceted variant.
    if parsed_url.path.rstrip("/") != parsed_src.path.rstrip("/"):
        return False

    # No query string → not a faceted variant.
    if not parsed_url.query:
        return False

    params = parse_qs(parsed_url.query, keep_blank_values=True)
    param_names = {k.lower() for k in params}

    # If any pagination parameter is present, treat as valid navigation.
    if param_names & _PAGINATION_PARAMS:
        return False

    # If ALL parameters are known filter params, it's a faceted-search URL.
    return bool(param_names) and param_names.issubset(_FILTER_PARAMS)


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
    parent_id: int | None = None,
    chunk: str = "",
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
            chunk=chunk,
            parent=parent_id,
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
    parent_id: int | None = None,
    chunk: str = "",
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
            chunk=chunk,
            parent=parent_id,
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
    Errors include the schema property description where available to help
    the LLM understand what is expected.
    """
    try:
        schema = _get_schema()
        validator = Draft202012Validator(schema)
        errors = []
        for err in validator.iter_errors([item]):
            path = " → ".join(str(p) for p in err.absolute_path) or "(root)"
            # Enrich the error message with schema context when available
            hint = ""
            prop_schema = err.schema
            if isinstance(prop_schema, dict):
                desc = prop_schema.get("description") or prop_schema.get("$comment")
                if desc:
                    # Trim to a concise hint
                    hint = f" (expected: {desc[:MAX_SCHEMA_HINT_LENGTH]})"
            errors.append(f"{path}: {err.message}{hint}")
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


def _summarise_page_for_structure(
    url: str,
    content: str,
    llm_client: Any,
    logger: "AgentLogger | None" = None,
    parent_id: int | None = None,
) -> dict[str, Any]:
    """Ask the LLM to summarise a single page's structure and content.

    Returns a dict with keys: page_type, description, training_items,
    navigation_links.  Used by ``compute_site_structure_summary``.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _STRUCTURE_PAGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Analyse this page: {url}\n\n"
                "Produce a JSON summary with:\n"
                '{"page_type": "catalog|listing|item|navigation|about|home|other", '
                '"description": "brief description of page content", '
                '"training_items": ['
                '{"title": "...", "description": "one sentence", "url": "..."}], '
                '"navigation_links": ['
                '{"url": "...", "type": "next_page|category|all_items|other", '
                '"description": "..."}]}\n\n'
                "For catalog/listing pages include the first 2–3 and last 1–2 "
                "visible training items so we can see the range of content. "
                "For navigation_links include ONLY standard pagination links "
                "(e.g. 'Next page', '?page=2') and genuine category landing pages. "
                "Do NOT include filter links, sort links, or any URL with query "
                "parameters like ?include_broken_links, ?across_all_spaces, "
                "?include_archived, ?sort, ?type, ?tag, ?category.\n\n"
                f"Page content:\n{content}"
            ),
        },
    ]
    result = _call_llm(
        llm_client,
        get_model_for_task("content_summary"),
        messages,
        logger=logger,
        task="content_summary",
        parent_id=parent_id,
    )
    # Guarantee expected keys even when the model returns a partial response.
    result.setdefault("page_type", "other")
    result.setdefault("description", "")
    result.setdefault("training_items", [])
    result.setdefault("navigation_links", [])
    return result


def _compile_site_structure(
    source_url: str,
    page_summaries: list[dict[str, Any]],
    llm_client: Any,
    logger: "AgentLogger | None" = None,
    parent_id: int | None = None,
) -> dict[str, Any]:
    """Ask the LLM to compile page summaries into a final structural summary.

    Returns a dict with keys: site_description, content_types.
    Used by ``compute_site_structure_summary``.
    """
    summaries_text = json.dumps(page_summaries, ensure_ascii=False, indent=2)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _STRUCTURE_COMPILE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Website: {source_url}\n\n"
                "Based on the following page summaries, produce a structural "
                "summary that will guide a metadata extraction agent.\n\n"
                "Output JSON with exactly this structure:\n"
                '{"site_description": "2–3 sentences about the website\'s purpose and content", '
                '"content_types": [{'
                '"type": "TrainingMaterial|CourseInstance|Course", '
                '"description": "what this type of content is on this site (1 sentence)", '
                '"primary_url": "main URL where items of this type are listed", '
                '"navigation": {'
                '"type": "paginated|categories|single_list|unknown", '
                '"urls": ["additional pagination or category URLs to crawl"], '
                '"description": "exact URL pattern to follow all pages, e.g. '
                "'Append ?page=N (N=2,3,…) to primary_url; stop when no Next link'"
                '"}, '
                '"examples": [{"title": "...", "description": "one sentence", "url": "..."}], '
                '"typical_structure": "one sentence: how items are laid out on the listing page"'
                '}]}\n\n'
                "Include 2–4 short examples per content type.\n"
                "In navigation.urls include ONLY standard pagination URLs or "
                "genuine category landing pages — NO filter/sort/facet URLs.\n\n"
                f"Page summaries:\n{summaries_text}"
            ),
        },
    ]
    result = _call_llm(
        llm_client,
        get_model_for_task("content_summary"),
        messages,
        logger=logger,
        task="content_summary",
        parent_id=parent_id,
    )
    result.setdefault("site_description", "")
    result.setdefault("content_types", [])
    return result


def compute_site_structure_summary(
    url: str,
    llm_client: Any,
    logger: "AgentLogger | None" = None,
    raw_html: bool = False,
) -> str:
    """Compute a rich structural summary for a training website (Phase 0).

    Crawls the primary URL plus up to ``MAX_STRUCTURE_PAGES - 1`` structural
    navigation pages (categories, pagination start/end, etc.).  For each page
    the beginning and end of the content are sent to an LLM to produce a brief
    page summary.  All page summaries are then compiled into a final structural
    summary JSON.

    This is an expensive operation but is **reused across extraction runs** —
    the caller should skip this step when a cached structural summary already
    exists.

    Args:
        url: Primary URL of the training website.
        llm_client: An OpenAI-compatible client instance.
        logger: Optional :class:`~app.agents.logger.AgentLogger` for structured
            progress events.
        raw_html: When ``True`` the LLM receives cleaned HTML instead of the
            default Markdown conversion.  Hashing is always performed on the
            Markdown representation regardless of this flag.

    Returns:
        JSON string with the structural summary (``schema_version="2"``).

    Raises:
        AccessDeniedError: If the primary URL is blocked or unreachable.
    """
    from datetime import datetime, timezone

    from app.agents.logger import AgentLogger

    _logger: AgentLogger = logger if logger is not None else AgentLogger()
    robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

    # Robots.txt check for the primary URL.
    if not _check_robots(url, robots_cache):
        raise AccessDeniedError(f"Crawling blocked by robots.txt for {url}")

    phase0_id = _logger.info("Phase 0: computing structural summary")
    parsed_primary = urlparse(url)
    primary_domain = parsed_primary.netloc

    # --- Fetch + summarise primary URL ---
    _logger.info(f"Fetching primary URL: {url}", parent=phase0_id)
    try:
        response = _fetch(url)
    except requests.RequestException as exc:
        raise AccessDeniedError(f"Failed to fetch {url}: {exc}") from exc

    if response.status_code in (401, 403):
        raise AccessDeniedError(
            f"Access denied (HTTP {response.status_code}) for {url}"
        )
    if not response.ok:
        raise AccessDeniedError(
            f"Could not retrieve {url} (HTTP {response.status_code})"
        )

    _logger.fetch(
        url=url,
        status_code=response.status_code,
        content_length=len(response.text),
        parent=phase0_id,
    )
    primary_content_text, primary_links = _page_content(response.text, url, raw_html)
    _logger.info(f"Primary page: {len(primary_content_text)} chars", parent=phase0_id)

    # Show beginning + end for large pages so the LLM sees the full range.
    if len(primary_content_text) > STRUCTURE_CONTENT_SIZE * 2:
        primary_content = (
            primary_content_text[:STRUCTURE_CONTENT_SIZE]
            + "\n\n[...middle content omitted...]\n\n"
            + primary_content_text[-STRUCTURE_CONTENT_SIZE:]
        )
    else:
        primary_content = primary_content_text[: STRUCTURE_CONTENT_SIZE * 2]

    _logger.info("Summarising primary page structure", parent=phase0_id)
    primary_summary = _summarise_page_for_structure(
        url, primary_content, llm_client, logger=_logger, parent_id=phase0_id
    )
    primary_summary["url"] = url
    page_summaries: list[dict[str, Any]] = [primary_summary]

    # --- Collect structural navigation links to explore ---
    # Priority 1: links identified by the LLM as navigation links.
    nav_link_urls: list[str] = [
        lnk["url"]
        for lnk in primary_summary.get("navigation_links", [])
        if lnk.get("url") and urlparse(lnk["url"]).netloc == primary_domain
    ]
    # Priority 2: additional same-domain links from the parsed page (as fallback).
    extra_links: list[str] = [
        link_url
        for link_url, _ in primary_links[:30]
        if urlparse(link_url).netloc == primary_domain and link_url != url
    ]

    # Deduplicate while preserving priority order.
    seen: set[str] = {url}
    links_to_follow: list[str] = []
    for link_url in nav_link_urls + extra_links:
        if link_url not in seen:
            seen.add(link_url)
            if not _is_non_content_url(link_url):
                links_to_follow.append(link_url)
        if len(links_to_follow) >= MAX_STRUCTURE_PAGES - 1:
            break

    _logger.info(
        f"Following {len(links_to_follow)} structural navigation link(s)",
        parent=phase0_id,
    )

    # --- Fetch + summarise each structural navigation page ---
    for nav_url in links_to_follow:
        if not _check_robots(nav_url, robots_cache):
            _logger.warn(f"Skipping {nav_url} (blocked by robots.txt)", parent=phase0_id)
            continue
        try:
            resp = _fetch(nav_url)
        except requests.RequestException as exc:
            _logger.warn(f"Could not fetch {nav_url}: {exc}", parent=phase0_id)
            continue
        if not resp.ok:
            _logger.warn(
                f"Skipping {nav_url} (HTTP {resp.status_code})", parent=phase0_id
            )
            continue

        _logger.fetch(
            url=nav_url,
            status_code=resp.status_code,
            content_length=len(resp.text),
            parent=phase0_id,
        )
        nav_content_text, _ = _page_content(resp.text, nav_url, raw_html)
        _logger.info(f"Summarising {nav_url} ({len(nav_content_text)} chars)", parent=phase0_id)

        if len(nav_content_text) > STRUCTURE_CONTENT_SIZE * 2:
            nav_content = (
                nav_content_text[:STRUCTURE_CONTENT_SIZE]
                + "\n\n[...middle content omitted...]\n\n"
                + nav_content_text[-STRUCTURE_CONTENT_SIZE:]
            )
        else:
            nav_content = nav_content_text[: STRUCTURE_CONTENT_SIZE * 2]

        page_sum = _summarise_page_for_structure(
            nav_url, nav_content, llm_client, logger=_logger, parent_id=phase0_id
        )
        page_sum["url"] = nav_url
        page_summaries.append(page_sum)

    # --- Compile into the final structural summary ---
    _logger.info(
        f"Compiling structural summary from {len(page_summaries)} page summary/ies",
        parent=phase0_id,
    )
    compiled = _compile_site_structure(
        url, page_summaries, llm_client, logger=_logger, parent_id=phase0_id
    )
    compiled["source_url"] = url
    compiled["source_domain"] = primary_domain
    compiled["computed_at"] = datetime.now(timezone.utc).isoformat()
    compiled["schema_version"] = "2"

    result_str = json.dumps(compiled, ensure_ascii=False, indent=2)
    _logger.info(
        f"Structural summary ready ({len(result_str)} chars)", parent=phase0_id
    )
    return result_str


def compute_structural_summary(
    items: list[dict[str, Any]],
    source_url: str,
    crawled_page_hashes: dict[str, str] | None = None,
) -> str:
    """Produce a compact site-structure summary for future incremental runs.

    .. deprecated::
        This function produces the legacy (schema_version=1) summary format.
        Use :func:`compute_site_structure_summary` instead for new code.
        This function is retained for backward compatibility only.

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

    See the module docstring for a description of the multi-phase pipeline.
    """

    def __init__(self) -> None:
        # Holds the AgentLogger for the duration of a run(); reset to None after.
        self._logger: AgentLogger | None = None
        self._raw_html: bool = False
        self.last_crawled_page_hashes: dict[str, str] = {}
        self.last_items_by_url: dict[str, dict[str, Any]] = {}

    def run(
        self,
        url: str,
        prompt: str | None = None,
        structural_summary: str | None = None,
        llm_client: Any = None,
        logger: AgentLogger | None = None,
        on_item: Callable[[dict[str, Any]], None] | None = None,
        raw_html: bool = False,
    ) -> list[dict[str, Any]]:
        """Extract Bioschemas JSON-LD from the given URL.

        Args:
            url: Primary source URL to crawl and extract from.
            prompt: Optional additional extraction instructions.
            structural_summary: JSON string produced by
                ``compute_site_structure_summary`` (schema_version=2) or the
                legacy ``compute_structural_summary``.  When provided, Phase 1
                starts from the ``primary_url`` fields listed in the summary
                and the LLM is focused on the described primary content types.
                Pass ``None`` to crawl from the root URL without guidance.
            llm_client: An OpenAI-compatible client instance (required).
            logger: Optional structured :class:`AgentLogger`.  A new one is
                created internally when not provided.
            on_item: Optional callback invoked with each fully processed
                JSON-LD item as it is produced (before the final list is
                returned).  Useful for streaming / partial result persistence.
            raw_html: When ``True`` the LLM receives cleaned HTML instead of
                the default Markdown conversion.  Hashing for change detection
                is always performed on the Markdown representation regardless
                of this flag.

        Returns:
            List of Bioschemas JSON-LD dicts.

        Raises:
            AccessDeniedError: Primary URL blocked by robots.txt or HTTP 401/403.
            NotTrainingContentError: No training content found after crawling.
        """
        self._logger = logger if logger is not None else AgentLogger()
        self._raw_html = raw_html
        self.last_crawled_page_hashes = {}
        self.last_items_by_url = {}

        if llm_client is None:
            raise ValueError("llm_client must be provided")

        # Log structural summary for debugging (truncated to avoid log spam).
        if structural_summary:
            preview = structural_summary[:500]
            suffix = "…" if len(structural_summary) > 500 else ""
            self._logger.info(f"Using structural summary: {preview}{suffix}")
        else:
            self._logger.info("No structural summary provided; crawling from root URL")

        # Determine starting URL(s) for Phase 1.
        # When using the new schema_version=2 format, start from the
        # primary_url of each listed content type rather than just the root URL.
        start_urls: list[str] = [url]
        summary_data: dict[str, Any] = {}
        previous_page_hashes: dict[str, str] = {}
        cached_items_by_url: dict[str, dict[str, Any]] = {}
        known_item_urls: list[str] = []
        if structural_summary:
            try:
                summary_data = json.loads(structural_summary)
                raw_hashes = summary_data.get("crawled_page_hashes")
                if isinstance(raw_hashes, dict):
                    previous_page_hashes = {
                        str(page_url): str(page_hash)
                        for page_url, page_hash in raw_hashes.items()
                        if page_url and page_hash
                    }
                raw_cached_items = summary_data.get("items_by_url")
                if isinstance(raw_cached_items, dict):
                    cached_items_by_url = {
                        str(item_url): item
                        for item_url, item in raw_cached_items.items()
                        if isinstance(item, dict)
                    }
                known_item_urls = [
                    str(item_url)
                    for item_url in summary_data.get("item_urls", [])
                    if item_url
                ]
                if summary_data.get("schema_version") == "2":
                    content_type_urls = [
                        ct["primary_url"]
                        for ct in summary_data.get("content_types", [])
                        if ct.get("primary_url")
                    ]
                    if content_type_urls:
                        start_urls = content_type_urls
                        self._logger.info(
                            f"Phase 1 starting from {len(start_urls)} content-type "
                            f"URL(s) listed in structural summary"
                        )
            except (json.JSONDecodeError, AttributeError, KeyError):
                pass

        if known_item_urls:
            combined_urls = [*start_urls, *known_item_urls]
            # Cap URL probes to bound crawl time and avoid runaway recursion on
            # very large catalogs; excess known item URLs are revisited in later runs.
            start_urls = list(dict.fromkeys(combined_urls))[:MAX_INCREMENTAL_START_URLS]
            self._logger.info(
                f"Incremental run will probe {len(start_urls)} known URL(s) "
                "(including previously discovered item URLs)"
            )

        state = _CrawlState()
        state.previous_page_hashes = previous_page_hashes

        # ----------------------------------------------------------------
        # Phase 1: CRAWL + DISCOVER
        # ----------------------------------------------------------------
        phase1_id = self._logger.info("Phase 1: crawl + discover")
        for start_url in start_urls:
            self._crawl_and_discover(
                start_url=start_url,
                structural_summary=structural_summary,
                llm_client=llm_client,
                state=state,
                is_primary=(start_url == url),
                parent_id=phase1_id,
            )

        # Ensure known items from previous runs are still represented, even when
        # unchanged pages are skipped during incremental crawls.
        known_discovered_urls = {d.url for d in state.discovered}
        for item_url in known_item_urls:
            if item_url in known_discovered_urls:
                continue
            if item_url not in cached_items_by_url:
                continue
            cached_item = cached_items_by_url[item_url]
            raw_item_type = cached_item.get("@type")
            item_type = str(
                raw_item_type[0]
                if isinstance(raw_item_type, list) and raw_item_type
                else (raw_item_type or "TrainingMaterial")
            )
            state.discovered.append(
                DiscoveredItem(
                    title=str(cached_item.get("name") or item_url),
                    url=item_url,
                    item_type=item_type,
                    source_url=item_url,
                    context="",
                )
            )

        if not state.discovered:
            raise NotTrainingContentError(
                f"No training content found at {url} (crawled "
                f"{len(state.pages)} page(s))"
            )

        self._logger.info(
            f"Discovery complete: {len(state.discovered)} item(s) found "
            f"across {len(state.pages)} page(s)",
            parent=phase1_id,
        )

        # ----------------------------------------------------------------
        # Phase 2 + 3 + 4: EXTRACT, REVIEW, VALIDATE per item
        # ----------------------------------------------------------------
        phase2_id = self._logger.info("Phase 2–4: extract, review, validate")
        final_items: list[dict[str, Any]] = []
        items_by_url_for_summary: dict[str, dict[str, Any]] = {}
        for item_info in state.discovered:
            item_id = self._logger.info(
                f"Item: {item_info.title!r} ({item_info.item_type})",
                parent=phase2_id,
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
                        parent=item_id,
                    )
                    if resp.ok:
                        item_html = resp.text
                except requests.RequestException as exc:
                    self._logger.warn(
                        f"Could not fetch detail page: {exc}",
                        parent=item_id,
                    )

            previous_item_hash = previous_page_hashes.get(item_info.url)
            cached_item_for_url = cached_items_by_url.get(item_info.url)
            item_page_hash = ""
            if previous_item_hash and cached_item_for_url is not None:
                item_markdown, _ = _html_to_markdown(item_html or "", item_info.url or url)
                item_page_hash = _content_hash(item_markdown)
            if (
                previous_item_hash
                and previous_item_hash == item_page_hash
                and cached_item_for_url is not None
            ):
                self._logger.info(
                    "Unchanged training material page detected; reusing cached metadata",
                    parent=item_id,
                )
                final_items.append(cached_item_for_url)
                items_by_url_for_summary[item_info.url] = cached_item_for_url
                if on_item:
                    on_item(cached_item_for_url)
                continue

            item_text, _ = _page_content(item_html or "", item_info.url or url, self._raw_html)
            relevant_chunks = self._select_relevant_item_chunks(
                item_info=item_info,
                content=item_text,
                llm_client=llm_client,
                parent_id=item_id,
            )
            review_content = _join_chunks_with_limit(
                relevant_chunks,
                MAX_EXTRACTION_CONTENT,
            )

            chunk_extractions: list[dict[str, Any]] = []
            for chunk_index, chunk_content in enumerate(relevant_chunks):
                if len(relevant_chunks) > 1:
                    self._logger.info(
                        f"Phase 2 chunk {chunk_index + 1}/{len(relevant_chunks)}",
                        parent=item_id,
                    )

                reasoning = self._reason_about_item(
                    item_info=item_info,
                    content=chunk_content,
                    llm_client=llm_client,
                    parent_id=item_id,
                )

                extracted_chunk = self._extract_item(
                    item_info=item_info,
                    content=chunk_content,
                    prompt=prompt,
                    reasoning=reasoning,
                    llm_client=llm_client,
                    parent_id=item_id,
                )
                if extracted_chunk:
                    chunk_extractions.append(extracted_chunk)

            if not chunk_extractions:
                self._logger.warn(
                    "Extraction returned empty result — skipping item",
                    parent=item_id,
                )
                continue

            extracted = self._merge_chunk_extractions(
                item_info=item_info,
                chunk_extractions=chunk_extractions,
                llm_client=llm_client,
                parent_id=item_id,
            )

            # --- Review ---
            reviewed = self._review_item(
                item=extracted,
                content=review_content,
                llm_client=llm_client,
                parent_id=item_id,
            )

            # --- Validate + fix ---
            # Apply programmatic TeSS conventions first so the schema can
            # validate them (dct:conformsTo, @context, @id).
            reviewed = _apply_tess_conventions(reviewed, item_info.url or url)
            errors = _validate_with_schema(reviewed)
            item_name = reviewed.get("name", item_info.title)
            if not errors:
                self._logger.validation(
                    item_name=item_name, errors=[], passed=True, parent=item_id
                )
            for attempt in range(MAX_FIX_ATTEMPTS):
                if not errors:
                    break
                self._logger.validation(
                    item_name=item_name, errors=errors, passed=False, parent=item_id
                )
                self._logger.info(
                    f"Validation: {len(errors)} error(s) — requesting fix"
                    f" (attempt {attempt + 1}/{MAX_FIX_ATTEMPTS})",
                    parent=item_id,
                )
                fixed = self._fix_item(
                    item=reviewed,
                    errors=errors,
                    llm_client=llm_client,
                    parent_id=item_id,
                )
                if fixed:
                    reviewed = _apply_tess_conventions(fixed, item_info.url or url)
                    errors = _validate_with_schema(reviewed)
                    if not errors:
                        self._logger.validation(
                            item_name=item_name, errors=[], passed=True, parent=item_id
                        )

            final_items.append(reviewed)
            if item_info.url:
                items_by_url_for_summary[item_info.url] = reviewed
            if on_item:
                on_item(reviewed)

        self.last_crawled_page_hashes = dict(state.page_hashes)
        self.last_items_by_url = items_by_url_for_summary
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
        parent_id: int | None = None,
    ) -> None:
        """Recursively crawl pages and populate *state.discovered*."""
        logger = self._logger
        if start_url in state.pages:
            return
        if len(state.pages) >= MAX_TOTAL_PAGES:
            if logger:
                logger.info(
                    f"Crawl limit ({MAX_TOTAL_PAGES} pages) reached",
                    parent=parent_id,
                )
            return

        # robots.txt check (raises for primary URL, logs and skips otherwise)
        if not _check_robots(start_url, state.robots_cache):
            if is_primary:
                raise AccessDeniedError(
                    f"Crawling blocked by robots.txt for {start_url}"
                )
            if logger:
                logger.warn(
                    f"Skipping {start_url} (blocked by robots.txt)",
                    parent=parent_id,
                )
            return

        label = "primary" if is_primary else f"depth-{depth}"
        page_id = logger.info(f"Fetch {label}: {start_url}", parent=parent_id) if logger else None
        try:
            response = _fetch(start_url)
        except requests.RequestException as exc:
            if is_primary:
                raise AccessDeniedError(f"Failed to fetch {start_url}: {exc}") from exc
            if logger:
                logger.warn(f"Skipping {start_url}: {exc}", parent=parent_id)
            return

        if logger:
            logger.fetch(
                url=start_url,
                status_code=response.status_code,
                content_length=len(response.text),
                parent=page_id,
            )

        if response.status_code in (401, 403):
            if is_primary:
                raise AccessDeniedError(
                    f"Access denied (HTTP {response.status_code}) for {start_url}"
                )
            if logger:
                logger.warn(
                    f"Skipping {start_url} (HTTP {response.status_code})",
                    parent=parent_id,
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
                logger.warn(msg, parent=parent_id)
            return

        html = response.text
        state.pages[start_url] = html

        # Always hash the stable Markdown content so that cosmetic HTML changes
        # (whitespace, inline styles, CDN URLs) don't trigger unnecessary
        # re-extraction.  When raw_html mode is active the LLM receives cleaned
        # HTML instead of Markdown, but the hash is still derived from Markdown.
        md_text, _ = _html_to_markdown(html, start_url)
        page_hash = _content_hash(md_text)
        state.page_hashes[start_url] = page_hash
        previous_page_hash = state.previous_page_hashes.get(start_url)
        if previous_page_hash and previous_page_hash == page_hash:
            if logger:
                logger.info(
                    "Page hash unchanged since previous crawl; skipping LLM analysis",
                    parent=page_id,
                )
            return
        content_text = _clean_html_for_llm(html, start_url)[0] if self._raw_html else md_text
        chunks = _chunk_text(content_text)
        total_chunks = len(chunks)
        if logger:
            logger.info(
                f"Page {len(state.pages)}/{MAX_TOTAL_PAGES}: "
                f"{len(html)} chars, {total_chunks} chunk(s), hash={page_hash[:12]}…",
                parent=page_id,
            )

        links_to_follow: list[str] = []
        previous_chunk_summary: str | None = None

        for chunk_idx, chunk_text in enumerate(chunks):
            result = self._classify_chunk(
                chunk_text=chunk_text,
                chunk_index=chunk_idx,
                total_chunks=total_chunks,
                source_url=start_url,
                structural_summary=structural_summary,
                previous_chunk_summary=previous_chunk_summary,
                llm_client=llm_client,
                parent_id=page_id,
            )

            if chunk_idx < total_chunks - 1:
                previous_chunk_summary = self._summarize_chunk_context(
                    chunk_text=chunk_text,
                    previous_chunk_summary=previous_chunk_summary,
                    llm_client=llm_client,
                    parent_id=page_id,
                )

            if not result.get("relevant", False):
                continue

            # Collect newly discovered items from this chunk
            chunk_id = (
                logger.info(
                    f"Chunk {chunk_idx + 1}/{total_chunks}: relevant",
                    parent=page_id,
                )
                if logger
                else None
            )
            for item_data in result.get("items", []):
                raw_item_url = item_data.get("url", start_url)
                # Resolve relative URLs against the page being crawled
                item_url = (
                    urljoin(start_url, raw_item_url)
                    if raw_item_url and not urlparse(raw_item_url).scheme
                    else raw_item_url
                )
                if item_url and _is_faceted_search_url(item_url, start_url):
                    if logger:
                        logger.warn(
                            f"Skipping faceted-search item URL: {item_url}",
                            parent=chunk_id,
                        )
                    continue
                if item_url and _is_non_content_url(item_url):
                    if logger:
                        logger.warn(
                            f"Skipping non-content item URL: {item_url}",
                            parent=chunk_id,
                        )
                    continue
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
                            parent=chunk_id,
                        )

            # Collect follow-links (deduplicated, capped)
            if depth < MAX_FOLLOW_DEPTH:
                for link_data in result.get("follow_links", [])[:MAX_LINKS_PER_CHUNK]:
                    raw_link_url = link_data.get("url", "")
                    if not raw_link_url:
                        continue
                    # Resolve relative URLs against the page being crawled
                    link_url = (
                        urljoin(start_url, raw_link_url)
                        if not urlparse(raw_link_url).scheme
                        else raw_link_url
                    )
                    if link_url in state.pages or link_url in links_to_follow:
                        continue
                    if _is_faceted_search_url(link_url, start_url):
                        if logger:
                            logger.warn(
                                f"Skipping faceted-search URL: {link_url}",
                                parent=parent_id,
                            )
                        continue
                    if _is_non_content_url(link_url):
                        if logger:
                            logger.warn(
                                f"Skipping non-content URL: {link_url}",
                                parent=parent_id,
                            )
                        continue
                    links_to_follow.append(link_url)

        # Recursively follow identified links
        for follow_url in links_to_follow:
            self._crawl_and_discover(
                start_url=follow_url,
                structural_summary=structural_summary,
                llm_client=llm_client,
                state=state,
                depth=depth + 1,
                parent_id=parent_id,
            )

    def _classify_chunk(
        self,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        source_url: str,
        structural_summary: str | None,
        previous_chunk_summary: str | None,
        llm_client: Any,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        """Ask the LLM to classify a text chunk and extract items + links."""
        guidance_note = ""
        nav_guidance = ""
        if structural_summary:
            try:
                summary = json.loads(structural_summary)
                if summary.get("schema_version") == "2":
                    # New rich format: provide site description + content type
                    # examples so the LLM focuses on primary training content only.
                    site_desc = summary.get("site_description", "")
                    content_types = summary.get("content_types", [])
                    types_text = ""
                    nav_patterns: list[str] = []
                    for ct in content_types:
                        ct_type = ct.get("type", "")
                        ct_desc = ct.get("description", "")
                        ct_struct = ct.get("typical_structure", "")
                        examples = ct.get("examples", [])
                        ex_lines = ", ".join(
                            f'"{e.get("title", "")}"' for e in examples[:2] if e.get("title")
                        )
                        types_text += (
                            f"\n  - {ct_type}: {ct_desc}"
                            + (f" (e.g. {ex_lines})" if ex_lines else "")
                            + (f". Layout: {ct_struct}" if ct_struct else "")
                        )
                        nav = ct.get("navigation", {})
                        nav_desc = nav.get("description", "")
                        if nav_desc:
                            nav_patterns.append(f"    {ct_type}: {nav_desc}")
                    guidance_note = (
                        f"\n\nSite description: {site_desc}"
                        f"\nFocus ONLY on extracting these primary training content types:{types_text}"
                        "\nIgnore any content that is not of these primary types "
                        "(e.g. 'About' pages, news, blog posts, general documentation).\n"
                    )
                    if nav_patterns:
                        nav_guidance = (
                            "\n\nNavigation patterns (follow ONLY links matching these):\n"
                            + "\n".join(nav_patterns)
                            + "\nDo NOT add any other links to follow_links.\n"
                        )
                else:
                    # Legacy format: show previously extracted item URLs so the
                    # LLM can focus on new/changed items.
                    prev_count = summary.get("item_count", "unknown")
                    item_urls = summary.get("item_urls", [])
                    guidance_note = (
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
                    f"from {source_url}.{guidance_note}{nav_guidance}\n\n"
                    "Identify:\n"
                    "1. Training materials, courses, or events mentioned\n"
                    "2. Links worth following to find more training content\n\n"
                    "Only treat an entry as an item when the chunk shows concrete "
                    "training content (e.g. card/row/detail text). Do NOT extract "
                    "filter/facet/tag links as items.\n\n"
                    "For follow_links: only include pagination links matching the "
                    "navigation pattern above (if provided), or links to genuinely "
                    "different content sections when no pattern is given. "
                    "Do NOT include faceted-search / filter links, creation pages, "
                    "admin pages, login pages, search pages, or any URL whose path "
                    "contains /new, /create, /edit, /delete, /admin, /sign_in, "
                    "/login, /register, /search, /api/.\n\n"
                    "If this chunk is navigation/filter UI only (no concrete item "
                    "content), set relevant=false and items=[].\n\n"
                    "Previous chunk carry-over context summary "
                    f"(may be empty): {previous_chunk_summary or '(none)'}\n\n"
                    "Output JSON:\n"
                    '{"relevant": true/false, "items": [{"title": "...", '
                    '"url": "...", "item_type": "TrainingMaterial|CourseInstance|Course", '
                    '"context": "excerpt mentioning this item"}], '
                    '"follow_links": [{"url": "...", "reason": "..."}], '
                    '"ignored_links": [{"url": "...", "reason": "facet_filter|auth|admin|other_non_content", '
                    '"context": "short evidence from chunk"}]}\n\n'
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
            parent_id=parent_id,
            chunk=chunk_text,
        )

    def _summarize_chunk_context(
        self,
        chunk_text: str,
        previous_chunk_summary: str | None,
        llm_client: Any,
        parent_id: int | None = None,
    ) -> str | None:
        """Summarise carry-over context needed to interpret the next chunk."""
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "Summarise contextual cues needed to interpret the next text "
                    "chunk from the same page. Keep only durable context such as "
                    "active section heading/scope, whether links are explicitly "
                    "irrelevant, and any list/table continuation cues. Explicitly "
                    "flag when the chunk is navigation/filter UI only."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Existing carry-over summary from previous chunk "
                    f"(may be empty): {previous_chunk_summary or '(none)'}\n\n"
                    "Current chunk:\n"
                    f"{chunk_text}\n\n"
                    "Return JSON: "
                    '{"continuation_context": "brief summary for the next chunk", '
                    '"chunk_signal": "content|navigation_only|mixed", '
                    '"ignore_link_patterns": ["facet_filter", "auth_or_admin", "..."]}'
                ),
            },
        ]
        result = _call_llm(
            llm_client,
            get_model_for_task("content_summary"),
            messages,
            logger=self._logger,
            task="content_summary",
            parent_id=parent_id,
            chunk=chunk_text,
        )
        summary_parts: list[str] = []
        summary = result.get("continuation_context")
        if isinstance(summary, str) and summary.strip():
            summary_parts.append(summary.strip())
        chunk_signal = result.get("chunk_signal")
        if chunk_signal in {"content", "navigation_only", "mixed"}:
            summary_parts.append(f"chunk_signal={chunk_signal}")
        ignore_patterns = result.get("ignore_link_patterns")
        if isinstance(ignore_patterns, list):
            clean_patterns = [
                stripped
                for pattern in ignore_patterns
                if isinstance(pattern, str) and (stripped := pattern.strip())
            ]
            if clean_patterns:
                summary_parts.append(
                    "ignore_link_patterns=" + ",".join(clean_patterns[:5])
                )
        if summary_parts:
            return " | ".join(summary_parts)
        return None

    def _classify_item_chunk_relevance(
        self,
        item_info: DiscoveredItem,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        llm_client: Any,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        """Classify whether a chunk is relevant for extracting one item."""
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "Decide if this chunk contains information useful for extracting "
                    "metadata for the specific training item. Focus only on this item, "
                    "not other courses/events. Return JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Item title: {item_info.title}\n"
                    f"Item URL: {item_info.url}\n"
                    f"Item type: {item_info.item_type}\n"
                    f"Item context hint: {item_info.context}\n\n"
                    f"Chunk {chunk_index + 1}/{total_chunks}:\n{chunk_text}\n\n"
                    'Return JSON: {"relevant": true/false, "reason": "..."}'
                ),
            },
        ]
        return _call_llm(
            llm_client,
            get_model_for_task("content_relevance"),
            messages,
            logger=self._logger,
            task="content_relevance",
            parent_id=parent_id,
            chunk=chunk_text,
        )

    def _select_relevant_item_chunks(
        self,
        item_info: DiscoveredItem,
        content: str,
        llm_client: Any,
        parent_id: int | None = None,
    ) -> list[str]:
        """Select item-relevant chunks using the fast relevance model."""
        if len(content) <= MAX_EXTRACTION_CONTENT:
            return [content]

        chunks = _chunk_text(
            content,
            chunk_size=EXTRACTION_CHUNK_SIZE,
            overlap=EXTRACTION_CHUNK_OVERLAP,
        )
        relevant_chunks: list[str] = []
        for chunk_index, chunk_text in enumerate(chunks):
            result = self._classify_item_chunk_relevance(
                item_info=item_info,
                chunk_text=chunk_text,
                chunk_index=chunk_index,
                total_chunks=len(chunks),
                llm_client=llm_client,
                parent_id=parent_id,
            )
            if result.get("relevant", False):
                relevant_chunks.append(chunk_text)

        if relevant_chunks:
            return relevant_chunks
        return [content[:MAX_EXTRACTION_CONTENT]]

    def _merge_chunk_extractions(
        self,
        item_info: DiscoveredItem,
        chunk_extractions: list[dict[str, Any]],
        llm_client: Any,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        """Merge chunk-level extraction outputs into one JSON-LD object."""
        if len(chunk_extractions) == 1:
            return chunk_extractions[0]

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Merge these partial JSON-LD extraction candidates into one "
                    "best final JSON-LD object for the same item.\n"
                    "Rules:\n"
                    "- Keep only information consistent across candidates or clearly "
                    "better supported.\n"
                    "- Prefer explicit values over inferred values.\n"
                    "- Do not invent fields not present in candidates.\n"
                    "- Resolve conflicts conservatively.\n\n"
                    f"Item title: {item_info.title}\n"
                    f"Item URL: {item_info.url}\n"
                    f"Item type: {item_info.item_type}\n\n"
                    "Candidates:\n"
                    f"{json.dumps(chunk_extractions)}"
                ),
            },
        ]
        return _call_llm(
            llm_client,
            get_model_for_task("json_ld_review"),
            messages,
            logger=self._logger,
            task="json_ld_review",
            parent_id=parent_id,
            chunk=json.dumps(chunk_extractions),
        )

    def _reason_about_item(
        self,
        item_info: DiscoveredItem,
        content: str,
        llm_client: Any,
        parent_id: int | None = None,
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
                    "IMPORTANT: only note information that is EXPLICITLY present "
                    "in the page — do not invent or assume any details.\n\n"
                    "Cover:\n"
                    "- Type (LearningResource for training material / tutorial, "
                    "CourseInstance for scheduled event / workshop)\n"
                    "- Title (exact wording from the page)\n"
                    "- Description (key points in 2–5 sentences)\n"
                    "- Authors / instructors (only if explicitly named on the page; "
                    "note full ORCID URL only if it appears on the page — do not "
                    "guess or look up ORCIDs)\n"
                    "- Dates (start/end; note if absent)\n"
                    "- Location or mode (online / onsite / blended)\n"
                    "- Scientific topics / keywords\n"
                    "- Educational level and target audience\n"
                    "- License (only if explicitly stated on the page)\n"
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
            parent_id=parent_id,
            chunk=content,
        )

    def _extract_item(
        self,
        item_info: DiscoveredItem,
        content: str,
        prompt: str | None,
        reasoning: str | None,
        llm_client: Any,
        parent_id: int | None = None,
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
            parent_id=parent_id,
            chunk=content,
        )

    def _review_item(
        self,
        item: dict[str, Any],
        content: str,
        llm_client: Any,
        parent_id: int | None = None,
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
            parent_id=parent_id,
        )
        return result if result else item

    def _fix_item(
        self,
        item: dict[str, Any],
        errors: list[str],
        llm_client: Any,
        parent_id: int | None = None,
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
                    "Quick reference for common fixes:\n"
                    "- @context must be: {\"@vocab\": \"https://schema.org/\", "
                    "\"dct\": \"http://purl.org/dc/terms/\"}\n"
                    "- @type must be one of: \"LearningResource\", "
                    "\"TrainingMaterial\", \"Course\", \"CourseInstance\"\n"
                    "- @id must be a stable URL string (use the item's own URL)\n"
                    "- name, description: required strings\n"
                    "- keywords: must be an array of strings, not a comma-separated string\n"
                    "- courseMode: must be an array containing only "
                    "\"online\", \"onsite\", or \"blended\"\n"
                    "- inLanguage: use BCP 47 code (e.g. \"en\", \"de\"), "
                    "not a full language name\n"
                    "- dates (startDate, endDate): use ISO 8601 format "
                    "(e.g. \"2024-03-15\" or \"2024-03-15T09:00:00\")\n"
                    "- license: use SPDX identifier (e.g. \"CC-BY-4.0\") "
                    "or full CC URL — ONLY if you can confirm it from page content\n"
                    "- author/@id: ORCID URI only if explicitly on the page\n\n"
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
            parent_id=parent_id,
        )
        return result if result else item
