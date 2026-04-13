"""Tests for the BioschemasExtractorAgent."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agents.bioschemas import (
    AccessDeniedError,
    BioschemasExtractorAgent,
    NotTrainingContentError,
    _content_hash,
    _extract_links,
    _chunk_text,
    _html_to_text,
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


def test_extract_links_finds_absolute_links() -> None:
    html = '<a href="https://example.com/page">Link</a>'
    links = _extract_links(html, "https://example.com")
    assert "https://example.com/page" in links


def test_extract_links_resolves_relative_links() -> None:
    html = '<a href="/courses/intro">Intro</a>'
    links = _extract_links(html, "https://example.com")
    assert "https://example.com/courses/intro" in links


def test_extract_links_skips_hash_and_mailto() -> None:
    html = '<a href="#top">Top</a><a href="mailto:a@b.com">Email</a>'
    links = _extract_links(html, "https://example.com")
    assert links == []


def test_extract_links_deduplicates() -> None:
    html = '<a href="/page">A</a><a href="/page">B</a>'
    links = _extract_links(html, "https://example.com")
    assert links.count("https://example.com/page") == 1


def test_html_to_text_strips_scripts() -> None:
    html = "<html><head><script>var x=1;</script></head><body><p>Hello</p></body></html>"
    text, _ = _html_to_text(html, "https://example.com")
    assert "Hello" in text
    assert "var x" not in text


def test_html_to_text_includes_link_context() -> None:
    html = '<a href="https://example.com/course">RNA-Seq Tutorial</a>'
    text, links = _html_to_text(html, "https://example.com")
    assert "RNA-Seq Tutorial" in text
    assert "https://example.com/course" in text
    assert len(links) == 1
    assert links[0][0] == "https://example.com/course"


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
    # 2. extraction
    # 3. review
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

    client = MockLLMClient([chunk_classification, extraction, review])

    logs: list[str] = []
    agent = BioschemasExtractorAgent()
    result = agent.run(
        url="https://example.com/training",
        llm_client=client,
        log_fn=logs.append,
    )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["@type"] == "LearningResource"
    assert result[0]["name"] == "Bioinformatics Workshop"
    assert len(logs) > 0


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
    # 2. Extraction missing required fields (name, description, keywords)
    bad_extraction = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "LearningResource",
        }
    )
    # 3. Review doesn't fix it
    reviewed_still_bad = bad_extraction
    # 4. Fix call returns a valid object
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

    client = MockLLMClient([chunk_class, bad_extraction, reviewed_still_bad, fixed])

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
    # 2. Extraction: missing dct:conformsTo, simple @context string
    extraction = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "LearningResource",
            "name": "Course",
            "description": "A great course.",
            "keywords": ["course"],
        }
    )
    # 3. Review returns same
    review = extraction
    # 4. Fix (in case schema validation triggers it) — returns same valid item
    fix = extraction

    client = MockLLMClient([chunk_class, extraction, review, fix])

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
