"""Tests for the BioschemasExtractorAgent."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agents.bioschemas import (
    AccessDeniedError,
    BioschemasExtractorAgent,
    DiscoveredItem,
    MAX_EXTRACTION_CONTENT,
    NotTrainingContentError,
    _content_hash,
    _html_to_markdown,
    _chunk_text,
    _is_faceted_search_url,
    _is_non_content_url,
    compute_site_structure_summary,
)


# ---------------------------------------------------------------------------
# Mock LLM infrastructure
# ---------------------------------------------------------------------------


class MockMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class MockChoice:
    def __init__(self, content: str) -> None:
        self.message = MockMessage(content)


class MockCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [MockChoice(content)]


class MockCompletions:
    def __init__(self, responses: list[str]) -> None:
        self._responses = iter(responses)

    def create(self, **kwargs: Any) -> MockCompletion:
        content = next(self._responses)
        return MockCompletion(content)


class MockChat:
    def __init__(self, responses: list[str]) -> None:
        self.completions = MockCompletions(responses)


class MockLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = MockChat(responses)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    text: str = "<html><body>Test page</body></html>", status_code: int = 200
) -> MagicMock:
    mock = MagicMock()
    mock.text = text
    mock.status_code = status_code
    mock.ok = status_code < 400
    return mock


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


def test_content_hash_is_deterministic() -> None:
    text = "hello world"
    assert _content_hash(text) == _content_hash(text)


def test_content_hash_differs_for_different_inputs() -> None:
    assert _content_hash("abc") != _content_hash("def")


def test_html_to_markdown_strips_scripts() -> None:
    html = "<html><head><script>var x=1;</script></head><body><p>Hello</p></body></html>"
    text, _ = _html_to_markdown(html, "https://example.com")
    assert "Hello" in text
    assert "var x" not in text


def test_html_to_markdown_includes_link_context() -> None:
    html = '<a href="https://example.com/course">RNA-Seq Tutorial</a>'
    text, links = _html_to_markdown(html, "https://example.com")
    assert "RNA-Seq Tutorial" in text
    assert "https://example.com/course" in text
    assert len(links) == 1
    assert links[0][0] == "https://example.com/course"


def test_html_to_markdown_resolves_relative_links() -> None:
    html = '<a href="/courses/intro">Intro</a>'
    _, links = _html_to_markdown(html, "https://example.com")
    assert any(url == "https://example.com/courses/intro" for url, _ in links)


def test_html_to_markdown_skips_hash_and_mailto() -> None:
    html = '<a href="#top">Top</a><a href="mailto:a@b.com">Email</a>'
    _, links = _html_to_markdown(html, "https://example.com")
    assert links == []


def test_html_to_markdown_deduplicates_links() -> None:
    html = '<a href="/page">A</a><a href="/page">B</a>'
    _, links = _html_to_markdown(html, "https://example.com")
    assert len([u for u, _ in links if u == "https://example.com/page"]) == 1


def test_html_to_markdown_keeps_images() -> None:
    """Images are kept (TeSS displays them in training material descriptions)."""
    html = '<img src="https://example.com/img.png" alt="diagram">'
    text, _ = _html_to_markdown(html, "https://example.com")
    assert "img.png" in text


def test_chunk_text_returns_single_chunk_for_short_text() -> None:
    text = "Short text."
    chunks = _chunk_text(text, chunk_size=100)
    assert chunks == [text]


def test_chunk_text_splits_long_text() -> None:
    # Build text definitely longer than chunk_size
    text = "This is a sentence. " * 300  # ~6000 chars
    chunks = _chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    # Each chunk should be at most chunk_size + some tolerance
    for chunk in chunks:
        assert len(chunk) <= 600  # chunk_size + some flexibility


def test_select_relevant_item_chunks_falls_back_when_none_classified_relevant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = BioschemasExtractorAgent()
    item_info = DiscoveredItem(
        title="AI",
        url="https://example.com/ai",
        item_type="TrainingMaterial",
        source_url="https://example.com/listing",
        context="ai",
    )
    text = (
        "Navigation menu | Filters | Login | Register\n"
        "Privacy policy | Terms of use | Cookie settings\n"
    ) * 500

    def _fake_all_irrelevant(
        _self: Any,
        item_info: Any,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        llm_client: Any,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        return {"relevant": False}

    monkeypatch.setattr(
        BioschemasExtractorAgent,
        "_classify_item_chunk_relevance",
        _fake_all_irrelevant,
    )

    selected = agent._select_relevant_item_chunks(
        item_info=item_info,
        content=text,
        llm_client=MockLLMClient([]),
    )

    assert selected == [text[:MAX_EXTRACTION_CONTENT]]


def test_merge_chunk_extractions_uses_llm_for_multiple_candidates() -> None:
    merged = json.dumps(
        {
            "@type": "LearningResource",
            "name": "Merged title",
            "description": "Merged description",
            "keywords": ["merged"],
            "url": "https://example.com/merged",
        }
    )
    client = MockLLMClient([merged])
    agent = BioschemasExtractorAgent()
    item_info = DiscoveredItem(
        title="Merged title",
        url="https://example.com/merged",
        item_type="TrainingMaterial",
        source_url="https://example.com/listing",
        context="context",
    )

    result = agent._merge_chunk_extractions(
        item_info=item_info,
        chunk_extractions=[
            {"name": "Merged title", "description": "Part A"},
            {"name": "Merged title", "description": "Part B"},
        ],
        llm_client=client,
    )

    assert result["name"] == "Merged title"
    assert result["description"] == "Merged description"


# ---------------------------------------------------------------------------
# Tests for _is_faceted_search_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,source_url,expected",
    [
        # Faceted search variants – same path, only filter params
        (
            "https://example.com/courses?category=bioinformatics",
            "https://example.com/courses",
            True,
        ),
        (
            "https://example.com/courses?sort=date&format=online",
            "https://example.com/courses",
            True,
        ),
        (
            "https://example.com/events?level=beginner&tag=python&sort=title",
            "https://example.com/events",
            True,
        ),
        (
            "https://example.com/events?q=bioinformatics",
            "https://example.com/events",
            True,
        ),
        # Pagination links – should NOT be treated as faceted search
        (
            "https://example.com/courses?page=2",
            "https://example.com/courses",
            False,
        ),
        (
            "https://example.com/courses?category=bio&page=3",
            "https://example.com/courses",
            False,
        ),
        (
            "https://example.com/courses?offset=20",
            "https://example.com/courses",
            False,
        ),
        # Different path – genuinely different page
        (
            "https://example.com/courses/python",
            "https://example.com/courses",
            False,
        ),
        (
            "https://example.com/about",
            "https://example.com/courses",
            False,
        ),
        # No query string – plain link, not faceted search
        (
            "https://example.com/courses",
            "https://example.com/courses",
            False,
        ),
        # Unknown param mixed with filter – not purely filter params, safe to follow
        (
            "https://example.com/courses?view=grid&myunknown=x",
            "https://example.com/courses",
            False,
        ),
        # Platform-specific unknown params: if they're the only params and we
        # don't know them, they're NOT treated as faceted-search (unknown param
        # means possibly a different content view, not a filter we recognise)
        (
            "https://example.com/materials?include_broken_links=true&myunknown=x",
            "https://example.com/materials",
            False,
        ),
    ],
)
def test_is_faceted_search_url(url: str, source_url: str, expected: bool) -> None:
    assert _is_faceted_search_url(url, source_url) is expected


# ---------------------------------------------------------------------------
# Tests for _is_non_content_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        # Non-content paths that should be filtered (CRUD / auth)
        ("https://example.com/materials/new", True),
        ("https://example.com/events/create", True),
        ("https://example.com/materials/123/edit", True),
        ("https://example.com/materials/123/delete", True),
        ("https://example.com/admin", True),
        ("https://example.com/admin/users", True),
        ("https://example.com/sign_in", True),
        ("https://example.com/login", True),
        ("https://example.com/register", True),
        # search and api are NOT filtered — they may yield useful content
        ("https://example.com/search", False),
        ("https://example.com/api/v1/materials", False),
        # Regular content paths that should NOT be filtered
        ("https://example.com/materials", False),
        ("https://example.com/materials?page=2", False),
        ("https://example.com/materials/my-tutorial", False),
        ("https://example.com/events/workshop-2024", False),
        ("https://example.com/topics/introduction", False),
        ("https://example.com/about", False),
    ],
)
def test_is_non_content_url(url: str, expected: bool) -> None:
    assert _is_non_content_url(url) is expected


# ---------------------------------------------------------------------------
# Agent tests
# ---------------------------------------------------------------------------


def test_agent_raises_access_denied_for_blocked_robots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocked robots.txt raises AccessDeniedError."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: False
    )

    agent = BioschemasExtractorAgent()
    client = MockLLMClient([])

    with pytest.raises(AccessDeniedError):
        agent.run(
            url="https://blocked-site.example.com/training",
            llm_client=client,
        )


def test_agent_raises_not_training_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM finding no items in any chunk raises NotTrainingContentError."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )

    mock_response = _make_response("<html><body>Some unrelated page</body></html>")
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: mock_response)

    # Chunk classification: not relevant, no items, no links to follow
    chunk_classification = json.dumps(
        {"relevant": False, "items": [], "follow_links": []}
    )

    client = MockLLMClient([chunk_classification])

    agent = BioschemasExtractorAgent()
    with pytest.raises(NotTrainingContentError):
        agent.run(
            url="https://example.com/not-training",
            llm_client=client,
        )


def test_agent_happy_path_returns_jsonld_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: agent returns a list of JSON-LD dicts."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )

    # Simple HTML with no links to follow (simplifies mock call count)
    page_html = """
    <html><body>
      <h1>Bioinformatics Workshop</h1>
      <p>An introduction to bioinformatics tools and techniques.</p>
    </body></html>
    """
    mock_response = _make_response(page_html)
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: mock_response)

    # LLM responses in order:
    # 1. chunk classification (1 chunk for small HTML)
    # 2. reasoning scratchpad (chain-of-thought, free text)
    # 3. extraction
    # 4. review
    chunk_classification = json.dumps(
        {
            "relevant": True,
            "items": [
                {
                    "title": "Bioinformatics Workshop",
                    "url": "https://example.com/training",
                    "item_type": "TrainingMaterial",
                    "context": "An introduction to bioinformatics tools.",
                }
            ],
            "follow_links": [],
        }
    )
    reasoning_text = (
        "Type: LearningResource. Title: Bioinformatics Workshop. "
        "Description: An introduction to bioinformatics tools and techniques."
    )
    extraction = json.dumps(
        {
            "@context": {"@vocab": "https://schema.org/", "dct": "http://purl.org/dc/terms/"},
            "@type": "LearningResource",
            "@id": "https://example.com/training",
            "name": "Bioinformatics Workshop",
            "description": "An introduction to bioinformatics tools and techniques.",
            "keywords": ["bioinformatics", "workshop"],
            "dct:conformsTo": {
                "@id": "https://bioschemas.org/profiles/TrainingMaterial/1.1-DRAFT"
            },
        }
    )
    review = extraction  # reviewed version same as extracted

    client = MockLLMClient([chunk_classification, reasoning_text, extraction, review])

    from app.agents.logger import AgentLogger

    run_logger = AgentLogger()
    agent = BioschemasExtractorAgent()
    result = agent.run(
        url="https://example.com/training",
        llm_client=client,
        logger=run_logger,
    )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["@type"] == "LearningResource"
    assert result[0]["name"] == "Bioinformatics Workshop"
    assert len(run_logger.events) > 0


def test_agent_validates_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """If extracted JSON-LD is missing required fields, agent re-prompts to fix them."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )

    page_html = "<html><body><h1>Workshop</h1></body></html>"
    mock_response = _make_response(page_html)
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: mock_response)

    # 1. chunk classification
    # 2. reasoning scratchpad (chain-of-thought, free text)
    # 3. Extraction missing required fields (name, description, keywords)
    # 4. Review doesn't fix it
    # 5. Fix call returns a valid object
    chunk_class = json.dumps(
        {
            "relevant": True,
            "items": [
                {
                    "title": "Workshop",
                    "url": "https://example.com/workshop",
                    "item_type": "TrainingMaterial",
                    "context": "Workshop",
                }
            ],
            "follow_links": [],
        }
    )
    reasoning_text = "Type: LearningResource. Title: Workshop. No dates visible."
    # 3. Extraction missing required fields (name, description, keywords)
    bad_extraction = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "LearningResource",
        }
    )
    # 4. Review doesn't fix it
    reviewed_still_bad = bad_extraction
    # 5. Fix call returns a valid object
    fixed = json.dumps(
        {
            "@context": {"@vocab": "https://schema.org/", "dct": "http://purl.org/dc/terms/"},
            "@type": "LearningResource",
            "@id": "https://example.com/workshop",
            "name": "Workshop",
            "description": "A hands-on workshop.",
            "keywords": ["workshop"],
            "dct:conformsTo": {
                "@id": "https://bioschemas.org/profiles/TrainingMaterial/1.1-DRAFT"
            },
        }
    )

    client = MockLLMClient([chunk_class, reasoning_text, bad_extraction, reviewed_still_bad, fixed])

    agent = BioschemasExtractorAgent()
    result = agent.run(
        url="https://example.com/workshop",
        llm_client=client,
    )

    assert isinstance(result, list)
    assert len(result) == 1
    # The fixed item should have been used
    assert result[0].get("name") == "Workshop"


def test_agent_applies_tess_conventions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent should ensure dct:conformsTo and proper @context are set."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )

    page_html = "<html><body><h1>Course</h1></body></html>"
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: _make_response(page_html))

    # 1. chunk classification
    # 2. reasoning scratchpad (chain-of-thought, free text)
    # 3. Extraction: missing dct:conformsTo, simple @context string
    # 4. Review returns same
    # 5. Fix (in case schema validation triggers it) — returns same valid item
    chunk_class = json.dumps(
        {
            "relevant": True,
            "items": [
                {
                    "title": "Course",
                    "url": "https://example.com/course",
                    "item_type": "TrainingMaterial",
                    "context": "A course.",
                }
            ],
            "follow_links": [],
        }
    )
    reasoning_text = "Type: LearningResource. Title: Course. A great course."
    # 3. Extraction: missing dct:conformsTo, simple @context string
    extraction = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "LearningResource",
            "name": "Course",
            "description": "A great course.",
            "keywords": ["course"],
        }
    )
    # 4. Review returns same
    review = extraction
    # 5. Fix (in case schema validation triggers it) — returns same valid item
    fix = extraction

    client = MockLLMClient([chunk_class, reasoning_text, extraction, review, fix])

    agent = BioschemasExtractorAgent()
    result = agent.run(
        url="https://example.com/course",
        llm_client=client,
    )

    assert len(result) == 1
    item = result[0]
    # TeSS conventions: dct:conformsTo must be set
    assert "dct:conformsTo" in item
    # @context should include dct namespace
    ctx = item.get("@context", {})
    assert isinstance(ctx, dict)
    assert "dct" in ctx


def test_agent_handles_http_403_as_access_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 403 on the primary URL raises AccessDeniedError."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )

    mock_response = _make_response(status_code=403)
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: mock_response)

    agent = BioschemasExtractorAgent()
    with pytest.raises(AccessDeniedError):
        agent.run(
            url="https://example.com/protected",
            llm_client=MockLLMClient([]),
        )


def test_agent_discovers_multiple_items_from_single_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunk classification can return multiple items; all are extracted."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )

    # Page listing two courses in one paragraph (or one table chunk)
    page_html = """
    <html><body>
      <p>
        Join <strong>Intro to Python</strong> (https://example.com/python) or
        <strong>Advanced R</strong> (https://example.com/r) this semester.
      </p>
    </body></html>
    """
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: _make_response(page_html))

    # Chunk classification returns TWO items from the single chunk
    chunk_classification = json.dumps(
        {
            "relevant": True,
            "items": [
                {
                    "title": "Intro to Python",
                    "url": "https://example.com/python",
                    "item_type": "TrainingMaterial",
                    "context": "Intro to Python course",
                },
                {
                    "title": "Advanced R",
                    "url": "https://example.com/r",
                    "item_type": "TrainingMaterial",
                    "context": "Advanced R course",
                },
            ],
            "follow_links": [],
        }
    )

    def _make_item(title: str, url: str) -> str:
        return json.dumps(
            {
                "@type": "LearningResource",
                "name": title,
                "description": f"A course on {title}.",
                "keywords": ["course"],
                "url": url,
            }
        )

    item1 = _make_item("Intro to Python", "https://example.com/python")
    item2 = _make_item("Advanced R", "https://example.com/r")

    # Responses: classify, reason1, extract1, review1, reason2, extract2, review2
    reasoning = "Type: LearningResource. Title visible. No dates."
    client = MockLLMClient(
        [chunk_classification, reasoning, item1, item1, reasoning, item2, item2]
    )

    agent = BioschemasExtractorAgent()
    result = agent.run(url="https://example.com/courses", llm_client=client)

    assert len(result) == 2
    names = {r["name"] for r in result}
    assert names == {"Intro to Python", "Advanced R"}


def test_agent_passes_chunk_context_summary_between_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunk carry-over summaries are threaded through chunk classification."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )
    monkeypatch.setattr("requests.get", lambda *a, **kw: _make_response("<html><body>x</body></html>"))
    monkeypatch.setattr(
        "app.agents.bioschemas._chunk_text",
        lambda text, chunk_size=0, overlap=0: ["chunk-1", "chunk-2", "chunk-3"],
    )

    seen_classifier_inputs: list[str | None] = []
    seen_summary_inputs: list[tuple[str, str | None]] = []

    def _fake_classify_chunk(
        self: BioschemasExtractorAgent,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        source_url: str,
        structural_summary: str | None,
        previous_chunk_summary: str | None,
        llm_client: Any,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        seen_classifier_inputs.append(previous_chunk_summary)
        if chunk_index < 2:
            return {"relevant": False, "items": [], "follow_links": []}
        return {
            "relevant": True,
            "items": [
                {
                    "title": "Chunked item",
                    "url": "https://example.com/chunked-item",
                    "item_type": "TrainingMaterial",
                    "context": "Found in later chunk",
                }
            ],
            "follow_links": [],
        }

    def _fake_summarize_chunk_context(
        self: BioschemasExtractorAgent,
        chunk_text: str,
        previous_chunk_summary: str | None,
        llm_client: Any,
        parent_id: int | None = None,
    ) -> str:
        seen_summary_inputs.append((chunk_text, previous_chunk_summary))
        return f"summary-for-{chunk_text}"

    monkeypatch.setattr(BioschemasExtractorAgent, "_classify_chunk", _fake_classify_chunk)
    monkeypatch.setattr(
        BioschemasExtractorAgent,
        "_summarize_chunk_context",
        _fake_summarize_chunk_context,
    )

    reasoning = "Type: LearningResource. Title: Chunked item."
    item = json.dumps(
        {
            "@type": "LearningResource",
            "name": "Chunked item",
            "description": "A chunked item.",
            "keywords": ["chunked"],
            "url": "https://example.com/chunked-item",
        }
    )
    client = MockLLMClient([reasoning, item, item])

    agent = BioschemasExtractorAgent()
    result = agent.run(url="https://example.com/courses", llm_client=client)

    assert len(result) == 1
    assert seen_classifier_inputs == [None, "summary-for-chunk-1", "summary-for-chunk-2"]
    assert seen_summary_inputs == [
        ("chunk-1", None),
        ("chunk-2", "summary-for-chunk-1"),
    ]


def test_agent_on_item_callback_called_per_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """on_item callback is invoked once per extracted item."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **kw: _make_response("<html><body>Workshop</body></html>"),
    )

    chunk_class = json.dumps(
        {
            "relevant": True,
            "items": [
                {
                    "title": "Workshop",
                    "url": "https://example.com/ws",
                    "item_type": "TrainingMaterial",
                    "context": "Workshop",
                }
            ],
            "follow_links": [],
        }
    )
    item = json.dumps(
        {
            "@type": "LearningResource",
            "name": "Workshop",
            "description": "A workshop.",
            "keywords": ["workshop"],
        }
    )
    reasoning = "Type: LearningResource. Title: Workshop."
    client = MockLLMClient([chunk_class, reasoning, item, item])

    received: list[dict[str, Any]] = []
    agent = BioschemasExtractorAgent()
    result = agent.run(
        url="https://example.com/ws",
        llm_client=client,
        on_item=received.append,
    )

    assert len(received) == 1
    assert received[0]["name"] == "Workshop"
    assert result == received


def test_agent_uses_llm_chunk_relevance_for_long_item_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long item pages use fast-model chunk selection before extraction."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **kw: _make_response("<html><body>Workshop listing</body></html>"),
    )

    long_item_text = "filler\n" * 9000
    chunk_nav = "navigation menu filter sort login register privacy policy"
    chunk_relevant = (
        "## Deep Learning Workshop\n"
        "This training workshop includes hands-on sessions and an agenda.\n"
    )
    chunk_footer = "footer links and legal notice"
    monkeypatch.setattr(
        "app.agents.bioschemas._page_content",
        lambda html, base_url, raw_html: (long_item_text, []),
    )
    monkeypatch.setattr(
        "app.agents.bioschemas._chunk_text",
        lambda text, chunk_size=0, overlap=0: (
            [chunk_nav, chunk_relevant, chunk_footer] if len(text) > 1000 else [text]
        ),
    )

    chunk_class = json.dumps(
        {
            "relevant": True,
            "items": [
                {
                    "title": "Deep Learning Workshop",
                    "url": "https://example.com/deep-learning",
                    "item_type": "TrainingMaterial",
                    "context": "workshop listing",
                }
            ],
            "follow_links": [],
        }
    )
    client = MockLLMClient([chunk_class])
    seen_chunk_indexes: list[int] = []

    captured_content: dict[str, str] = {}

    def _fake_relevance(
        _self: Any,
        item_info: Any,
        chunk_text: str,
        chunk_index: int,
        total_chunks: int,
        llm_client: Any,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        seen_chunk_indexes.append(chunk_index)
        return {"relevant": chunk_text == chunk_relevant}

    def _fake_reason(
        self: BioschemasExtractorAgent,
        item_info: Any,
        content: str,
        llm_client: Any,
        parent_id: int | None = None,
    ) -> str:
        captured_content["value"] = content
        return "reasoning"

    def _fake_extract(
        self: BioschemasExtractorAgent,
        item_info: Any,
        content: str,
        prompt: str | None,
        reasoning: str,
        llm_client: Any,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        return {
            "@type": "LearningResource",
            "name": "Deep Learning Workshop",
            "description": "A workshop.",
            "keywords": ["workshop"],
            "url": "https://example.com/deep-learning",
        }

    monkeypatch.setattr(
        BioschemasExtractorAgent,
        "_classify_item_chunk_relevance",
        _fake_relevance,
    )
    monkeypatch.setattr(BioschemasExtractorAgent, "_reason_about_item", _fake_reason)
    monkeypatch.setattr(BioschemasExtractorAgent, "_extract_item", _fake_extract)
    monkeypatch.setattr(
        BioschemasExtractorAgent,
        "_review_item",
        lambda self, item, content, llm_client, parent_id=None: item,
    )

    agent = BioschemasExtractorAgent()
    result = agent.run(url="https://example.com/listing", llm_client=client)

    assert len(result) == 1
    assert seen_chunk_indexes == [0, 1, 2]
    assert "Deep Learning Workshop" in captured_content["value"]
    assert "hands-on sessions and an agenda" in captured_content["value"]
    assert chunk_nav not in captured_content["value"]


# ---------------------------------------------------------------------------
# Tests for compute_site_structure_summary
# ---------------------------------------------------------------------------


def test_compute_site_structure_summary_returns_schema_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compute_site_structure_summary returns a JSON string with schema_version=2."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )

    # Use a page with only external links so no additional same-domain pages
    # are crawled and the mock client only needs two responses.
    catalog_html = """
    <html><body>
      <h1>Training Catalogue</h1>
      <p>Courses available at this site.</p>
      <a href="https://other-domain.example/python">External Python course</a>
    </body></html>
    """
    monkeypatch.setattr("requests.get", lambda *a, **kw: _make_response(catalog_html))

    # LLM responses:
    # 1. _summarise_page_for_structure for the primary URL (navigation_links=[])
    # 2. _compile_site_structure
    page_summary = json.dumps(
        {
            "page_type": "catalog",
            "description": "Catalogue of training courses",
            "training_items": [
                {"title": "Python course", "description": "Python basics", "url": "https://example.com/courses/python"},
            ],
            "navigation_links": [],
        }
    )
    compiled_summary = json.dumps(
        {
            "site_description": "A website offering scientific training courses",
            "content_types": [
                {
                    "type": "TrainingMaterial",
                    "description": "Online training courses",
                    "primary_url": "https://example.com/courses",
                    "navigation": {
                        "type": "single_list",
                        "urls": [],
                        "description": "single list",
                    },
                    "examples": [
                        {"title": "Python course", "description": "Python basics", "url": "https://example.com/courses/python"},
                    ],
                    "typical_structure": "Title, level badge, Start link",
                }
            ],
        }
    )

    client = MockLLMClient([page_summary, compiled_summary])

    from app.agents.logger import AgentLogger, InfoEvent, WarnEvent

    agent_logger = AgentLogger()
    result_str = compute_site_structure_summary(
        url="https://example.com/courses",
        llm_client=client,
        logger=agent_logger,
    )

    result = json.loads(result_str)
    assert result["schema_version"] == "2"
    assert result["source_url"] == "https://example.com/courses"
    assert "site_description" in result
    assert isinstance(result["content_types"], list)
    assert len(result["content_types"]) >= 1
    # Structural summary should be logged
    logs = [
        ev.message for ev in agent_logger.events if isinstance(ev, (InfoEvent, WarnEvent))
    ]
    assert any("structural summary" in msg.lower() for msg in logs)


def test_compute_site_structure_summary_raises_on_access_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compute_site_structure_summary raises AccessDeniedError when robots.txt blocks."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: False
    )

    with pytest.raises(AccessDeniedError):
        compute_site_structure_summary(
            url="https://blocked.example.com/",
            llm_client=MockLLMClient([]),
        )


def test_compute_site_structure_summary_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compute_site_structure_summary raises AccessDeniedError for HTTP 403."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )
    monkeypatch.setattr("requests.get", lambda *a, **kw: _make_response(status_code=403))

    with pytest.raises(AccessDeniedError):
        compute_site_structure_summary(
            url="https://example.com/protected",
            llm_client=MockLLMClient([]),
        )


def test_agent_uses_structural_summary_start_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a v2 structural summary, Phase 1 starts from content_type primary_url."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr(
        "urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True
    )

    fetched_urls: list[str] = []

    def _mock_get(url: str, **kwargs: Any) -> MagicMock:
        fetched_urls.append(url)
        return _make_response("<html><body><h1>Course Catalogue</h1></body></html>")

    monkeypatch.setattr("requests.get", _mock_get)

    structural_summary = json.dumps(
        {
            "schema_version": "2",
            "source_url": "https://example.com",
            "source_domain": "example.com",
            "computed_at": "2024-01-01T00:00:00+00:00",
            "site_description": "A training website",
            "content_types": [
                {
                    "type": "TrainingMaterial",
                    "description": "Tutorials",
                    "primary_url": "https://example.com/catalogue",
                    "navigation": {"type": "single_list", "urls": [], "description": ""},
                    "examples": [],
                    "typical_structure": "title + link",
                }
            ],
        }
    )

    # Chunk classification returns no items (just to trigger discovery)
    chunk_classification = json.dumps({"relevant": False, "items": [], "follow_links": []})
    client = MockLLMClient([chunk_classification])

    agent = BioschemasExtractorAgent()
    with pytest.raises(NotTrainingContentError):
        agent.run(
            url="https://example.com",
            llm_client=client,
            structural_summary=structural_summary,
        )

    # Phase 1 should have fetched the primary_url from the structural summary,
    # NOT the root URL.
    assert "https://example.com/catalogue" in fetched_urls
