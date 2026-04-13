"""Bioschemas extraction agent.

Reads web pages, follows links, and produces Bioschemas JSON-LD for training
materials and course instances.  This module must NOT import Flask.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.robotparser
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FOLLOW_LINKS = 5
MAX_PAGINATED_LINKS = 20
MAX_FOLLOW_DEPTH = 2

_USER_AGENT = "BioschemasMetadataGenerator/1.0 (+https://github.com/pan-training/llm-metadata-generator)"

# ---------------------------------------------------------------------------
# Embedded system prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert at extracting structured metadata from web pages about training \
materials and courses, following the Bioschemas and Schema.org standards as used \
by TeSS (Training eSupport System).

## Bioschemas profiles you must follow

### TrainingMaterial (LearningResource)
Required fields:
- @context: "https://schema.org" (plus optional dct namespace)
- @type: "LearningResource"
- @id: canonical URL of the resource (IRI)
- name: human-readable title (string)
- description: plain text or HTML description (string)
- keywords: list of relevant keywords (array of strings)
- dct:conformsTo: {"@id": "https://bioschemas.org/profiles/TrainingMaterial/1.0-RELEASE"}

Strongly recommended fields:
- url: canonical URL (string)
- author: array of Person/Organization objects
- license: URL or CreativeWork
- inLanguage: language code (e.g. "en")
- audience: array of EducationalAudience objects with audienceType
- teaches: array of strings or DefinedTerms (learning outcomes)
- about: array of DefinedTerms for scientific topics (EDAM preferred)
- mentions: array of Tool objects (bio.tools entries when relevant)

### CourseInstance
Required fields:
- @context: "https://schema.org"
- @type: "CourseInstance"
- @id: canonical IRI of the instance
- name: title
- description: text description
- courseMode: array — use "online", "onsite", "blended" (TeSS accepted values)
- location: Place object with PostalAddress, or VirtualLocation for online events
- dct:conformsTo: {"@id": "https://bioschemas.org/profiles/CourseInstance/1.0-RELEASE"}

Strongly recommended:
- startDate, endDate: ISO 8601 date/datetime strings
- url: canonical URL
- organizer / provider: Organization or Person
- maximumAttendeeCapacity: integer

## TeSS-specific conventions

- Always include `dct:conformsTo` so TeSS can identify the profile version.
  Use namespace prefix in @context: {"@vocab": "https://schema.org/", "dct": "http://purl.org/dc/terms/"}
- For people with ORCID, use the ORCID URL as @id:
  {"@type": "Person", "@id": "https://orcid.org/0000-0001-2345-6789", "name": "..."}
- For keywords: provide an array of lowercase strings (TeSS splits on commas if given as one string).
- For scientific topics: use EDAM ontology DefinedTerms when possible:
  {"@type": "DefinedTerm", "@id": "http://edamontology.org/topic_0091", "name": "Bioinformatics"}
- DOIs: include as @id when available (e.g. "https://doi.org/10.12345/...").
- courseMode values: "online" (virtual), "onsite" (in-person), "blended" (hybrid).
- TeSS normalises HTTPS schema.org URIs to HTTP — use "https://schema.org/" as @context.

## Output format

Always output valid JSON (no markdown fences, no explanatory text around JSON).
For discovery tasks: output {"training_items": [{"title": "...", "url": "...", "type": "TrainingMaterial|CourseInstance"}]}
For link decisions: output {"follow": ["url1", "url2", ...]}
For extraction: output a single JSON-LD object.
For review: output the improved JSON-LD object (full, not a diff).
For structural summaries: output {"summary": "...", "items": [...]}
"""

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AccessDeniedError(Exception):
    """Raised when robots.txt blocks crawling of the primary source URL."""


class NotTrainingContentError(Exception):
    """Raised when no training content is found on the page."""


class MultipleTrainingContentError(Exception):
    """Raised in single-resource mode when multiple primary candidates exist."""


# ---------------------------------------------------------------------------
# HTML link parser
# ---------------------------------------------------------------------------


class _LinkParser(HTMLParser):
    """Minimal HTML parser that collects href values from <a> tags."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)


def _extract_links(html: str, base_url: str) -> list[str]:
    """Return absolute URLs extracted from <a href="..."> tags in *html*."""
    parser = _LinkParser()
    parser.feed(html)
    links: list[str] = []
    for href in parser.links:
        href = href.strip()
        if href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        # Only keep http/https links
        if urlparse(absolute).scheme in ("http", "https"):
            links.append(absolute)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for link in links:
        if link not in seen:
            seen.add(link)
            unique.append(link)
    return unique


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _fetch(url: str) -> requests.Response:
    """Fetch a URL and return the response."""
    return requests.get(
        url,
        headers={"User-Agent": _USER_AGENT},
        timeout=15,
    )


def _check_robots(url: str) -> bool:
    """Return True if crawling *url* is allowed, False if blocked."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        # If robots.txt can't be fetched, assume allowed
        return True
    return rp.can_fetch(_USER_AGENT, url)


def _content_hash(text: str) -> str:
    """Return a stable SHA-256 hex digest for the given text."""
    return hashlib.sha256(text.encode()).hexdigest()


def _call_llm(
    client: Any,
    model: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Call the LLM chat completions API and parse the JSON response."""
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    try:
        return json.loads(content)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        # Try to extract JSON from the response if it contains extra text
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass
        return {}


def _apply_tess_conventions(item: dict[str, Any], url: str) -> dict[str, Any]:
    """Ensure TeSS-required fields are set on a JSON-LD item."""
    # Ensure proper @context with dct namespace
    if "@context" not in item or item["@context"] == "https://schema.org":
        item["@context"] = {
            "@vocab": "https://schema.org/",
            "dct": "http://purl.org/dc/terms/",
        }

    # Ensure dct:conformsTo is set based on @type
    if "dct:conformsTo" not in item:
        item_type = item.get("@type", "")
        if "CourseInstance" in item_type:
            profile_iri = "https://bioschemas.org/profiles/CourseInstance/1.0-RELEASE"
        else:
            profile_iri = "https://bioschemas.org/profiles/TrainingMaterial/1.0-RELEASE"
        item["dct:conformsTo"] = {"@id": profile_iri}

    # Ensure @id is set
    if "@id" not in item:
        item["@id"] = item.get("url", url)

    return item


def _validate_required_fields(item: dict[str, Any]) -> list[str]:
    """Return a list of validation error messages for the given JSON-LD item."""
    errors: list[str] = []
    item_type = item.get("@type", "")

    if not item_type:
        errors.append("Missing required field: @type (must be LearningResource or CourseInstance)")

    if not item.get("name"):
        errors.append("Missing required field: name")

    if not item.get("description"):
        errors.append("Missing required field: description")

    if not item.get("keywords"):
        errors.append("Missing required field: keywords")

    if "CourseInstance" in item_type:
        if not item.get("courseMode"):
            errors.append("Missing required field for CourseInstance: courseMode")

    return errors


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------


class BioschemasExtractorAgent:
    """Extracts Bioschemas JSON-LD from web pages about training materials."""

    def run(
        self,
        url: str,
        prompt: str | None = None,
        update_level: int = 1,
        structural_summary: str | None = None,
        llm_client: Any = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Extract Bioschemas JSON-LD from the given URL.

        Args:
            url: The source URL to extract from.
            prompt: Optional additional instructions for the extraction agent.
            update_level: 0=no update, 1=incremental, 2=full refresh.
            structural_summary: Summary of the last crawl (used at level 1).
            llm_client: An OpenAI-compatible client.  If None a real client
                must be provided via the Flask app config.
            log_fn: Optional callable to receive progress log messages.

        Returns:
            A list of Bioschemas JSON-LD dicts.

        Raises:
            AccessDeniedError: If robots.txt blocks the primary URL.
            NotTrainingContentError: If no training content found.
        """

        def log(msg: str) -> None:
            if log_fn:
                log_fn(msg)

        if llm_client is None:
            raise ValueError("llm_client must be provided")

        from app.agents import get_model_for_task

        # ----------------------------------------------------------------
        # Step 1: Check robots.txt
        # ----------------------------------------------------------------
        log(f"Checking robots.txt for {url}")
        if not _check_robots(url):
            raise AccessDeniedError(f"Crawling blocked by robots.txt for {url}")

        # ----------------------------------------------------------------
        # Step 2: Fetch primary page
        # ----------------------------------------------------------------
        log(f"Fetching {url}")
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
                f"HTTP {response.status_code} fetching {url}"
            )

        page_html = response.text
        page_hash = _content_hash(page_html)
        log(f"Fetched {len(page_html)} chars, hash={page_hash[:12]}…")

        # ----------------------------------------------------------------
        # Step 3: DISCOVERY – identify training items
        # ----------------------------------------------------------------
        log("Starting DISCOVERY phase")
        discovery_messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
        ]

        discovery_user_content = (
            f"Analyse the following web page content from {url} and identify ALL "
            "training materials and courses listed on it.\n\n"
            "Output JSON with this structure:\n"
            '{"training_items": [{"title": "...", "url": "...", "type": "TrainingMaterial|CourseInstance"}]}\n\n'
        )
        if update_level == 1 and structural_summary:
            discovery_user_content += (
                "Previous crawl structural summary (focus on changed/new items):\n"
                f"{structural_summary}\n\n"
            )
        if prompt:
            discovery_user_content += f"Additional instructions: {prompt}\n\n"
        discovery_user_content += f"Page content:\n{page_html[:8000]}"

        discovery_messages.append({"role": "user", "content": discovery_user_content})

        log("Calling LLM for discovery")
        discovery_result = _call_llm(
            llm_client,
            get_model_for_task("content_summary"),
            discovery_messages,
        )
        training_items: list[dict[str, Any]] = discovery_result.get("training_items", [])
        log(f"Discovered {len(training_items)} training item(s)")

        if not training_items:
            raise NotTrainingContentError(
                f"No training content found on {url}"
            )

        # ----------------------------------------------------------------
        # Step 4: LINK FOLLOWING – decide which additional links to crawl
        # ----------------------------------------------------------------
        all_links = _extract_links(page_html, url)
        log(f"Found {len(all_links)} links on page")

        followed_content: dict[str, str] = {}
        if all_links:
            log("Calling LLM for link decision")
            link_messages: list[dict[str, str]] = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Source page: {url}\n"
                        f"Training items found: {json.dumps(training_items)}\n\n"
                        "From the following links found on the page, decide which ones "
                        f"(at most {MAX_FOLLOW_LINKS}) are worth following to get more detail "
                        "about the training items listed above. Only follow links that are "
                        "direct detail pages for the items above or pagination links.\n\n"
                        "Output JSON: {\"follow\": [\"url1\", \"url2\", ...]}\n\n"
                        f"Links:\n" + "\n".join(all_links[:100])
                    ),
                },
            ]
            link_result = _call_llm(
                llm_client,
                get_model_for_task("link_decision"),
                link_messages,
            )
            follow_urls: list[str] = link_result.get("follow", [])[:MAX_FOLLOW_LINKS]
            log(f"Following {len(follow_urls)} additional links")

            for follow_url in follow_urls:
                if not _check_robots(follow_url):
                    log(f"Skipping {follow_url} (blocked by robots.txt)")
                    continue
                try:
                    follow_resp = _fetch(follow_url)
                    if follow_resp.ok:
                        followed_content[follow_url] = follow_resp.text
                        log(f"Fetched follow link: {follow_url}")
                except requests.RequestException as exc:
                    log(f"Failed to fetch {follow_url}: {exc}")

        # ----------------------------------------------------------------
        # Step 5: EXTRACTION – extract JSON-LD for each item
        # ----------------------------------------------------------------
        # TODO: ontology vector search will be added here when ontology indexing
        # is implemented (see ontology issue). For now, no ontology context is
        # provided to the extraction prompt.

        extracted_items: list[dict[str, Any]] = []
        for item_info in training_items:
            item_url = item_info.get("url", url)
            item_title = item_info.get("title", "")
            item_type = item_info.get("type", "TrainingMaterial")
            log(f"Extracting JSON-LD for: {item_title}")

            # Gather content for this item
            item_html = followed_content.get(item_url, page_html)
            if item_url != url and item_url not in followed_content:
                # Try to fetch the detail page if not already fetched
                if _check_robots(item_url):
                    try:
                        detail_resp = _fetch(item_url)
                        if detail_resp.ok:
                            item_html = detail_resp.text
                            log(f"Fetched detail page: {item_url}")
                    except requests.RequestException as exc:
                        log(f"Could not fetch detail page {item_url}: {exc}")

            extraction_user_content = (
                f"Extract Bioschemas JSON-LD metadata for the training item below.\n\n"
                f"Item title: {item_title}\n"
                f"Item URL: {item_url}\n"
                f"Item type: {item_type}\n\n"
                "Output a single valid JSON-LD object (no markdown, no extra text).\n"
            )
            if prompt:
                extraction_user_content += f"Additional instructions: {prompt}\n\n"
            extraction_user_content += f"Page content:\n{item_html[:8000]}"

            extraction_messages: list[dict[str, str]] = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": extraction_user_content},
            ]

            extracted = _call_llm(
                llm_client,
                get_model_for_task("json_ld_review"),
                extraction_messages,
            )

            if extracted:
                extracted_items.append(extracted)

        log(f"Extracted {len(extracted_items)} JSON-LD item(s)")

        # ----------------------------------------------------------------
        # Step 6: REVIEW – self-critical review of each item
        # ----------------------------------------------------------------
        reviewed_items: list[dict[str, Any]] = []
        for item in extracted_items:
            log(f"Reviewing: {item.get('name', 'unnamed')}")
            review_messages: list[dict[str, str]] = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Critically review the following Bioschemas JSON-LD object and improve it. "
                        "Check for: missing required fields, incorrect @type values, "
                        "missing dct:conformsTo, malformed keywords (should be an array), "
                        "missing @context, and other issues.\n\n"
                        "Return the complete improved JSON-LD object.\n\n"
                        f"Current JSON-LD:\n{json.dumps(item, indent=2)}"
                    ),
                },
            ]
            reviewed = _call_llm(
                llm_client,
                get_model_for_task("json_ld_review"),
                review_messages,
            )
            reviewed_items.append(reviewed if reviewed else item)

        # ----------------------------------------------------------------
        # Step 7: VALIDATION + re-prompting
        # ----------------------------------------------------------------
        final_items: list[dict[str, Any]] = []
        for item in reviewed_items:
            errors = _validate_required_fields(item)
            if errors:
                log(f"Validation errors for '{item.get('name', 'unnamed')}': {errors}")
                fix_messages: list[dict[str, str]] = [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "The following JSON-LD object has validation errors. "
                            "Fix ALL of them and return the corrected JSON-LD object.\n\n"
                            f"Errors:\n" + "\n".join(f"- {e}" for e in errors) + "\n\n"
                            f"Current JSON-LD:\n{json.dumps(item, indent=2)}"
                        ),
                    },
                ]
                fixed = _call_llm(
                    llm_client,
                    get_model_for_task("json_ld_review"),
                    fix_messages,
                )
                item = fixed if fixed else item

            # Step 8: Apply TeSS conventions
            item = _apply_tess_conventions(item, url)
            final_items.append(item)

        log(f"Completed extraction: {len(final_items)} item(s) ready")
        return final_items


def compute_structural_summary(
    training_items: list[dict[str, Any]],
    url: str,
) -> str:
    """Produce a compact structural summary for the incremental update cache."""
    return json.dumps(
        {
            "source_url": url,
            "item_count": len(training_items),
            "items": [
                {
                    "title": item.get("name", item.get("title", "")),
                    "url": item.get("url", item.get("@id", "")),
                    "type": item.get("@type", ""),
                }
                for item in training_items
            ],
        }
    )
