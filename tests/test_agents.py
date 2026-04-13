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


def _make_response(text: str = "<html><body>Test page</body></html>", status_code: int = 200) -> MagicMock:
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


# ---------------------------------------------------------------------------
# Agent tests
# ---------------------------------------------------------------------------


def test_agent_raises_access_denied_for_blocked_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blocked robots.txt raises AccessDeniedError."""
    mock_rp = MagicMock()
    mock_rp.can_fetch.return_value = False

    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: False)

    agent = BioschemasExtractorAgent()
    client = MockLLMClient([])

    with pytest.raises(AccessDeniedError):
        agent.run(
            url="https://blocked-site.example.com/training",
            llm_client=client,
        )


def test_agent_raises_not_training_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM returning empty training_items raises NotTrainingContentError."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True)

    mock_response = _make_response("<html><body>Some unrelated page</body></html>")
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: mock_response)

    # Discovery returns no items; link decision has no links to decide on
    discovery_response = json.dumps({"training_items": []})
    link_response = json.dumps({"follow": []})

    client = MockLLMClient([discovery_response, link_response])

    agent = BioschemasExtractorAgent()
    with pytest.raises(NotTrainingContentError):
        agent.run(
            url="https://example.com/not-training",
            llm_client=client,
        )


def test_agent_happy_path_returns_jsonld_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: agent returns a list of JSON-LD dicts."""
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.read", lambda self: None)
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True)

    page_html = """
    <html><body>
      <h1>Bioinformatics Workshop</h1>
      <p>An introduction to bioinformatics tools.</p>
      <a href="https://example.com/module1">Module 1</a>
    </body></html>
    """
    mock_response = _make_response(page_html)
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: mock_response)

    # LLM responses in order:
    # 1. discovery
    # 2. link decision
    # 3. extraction (for the one item)
    # 4. review
    # 5. validation fix (none needed, but we provide extra for safety)
    discovery = json.dumps({
        "training_items": [
            {"title": "Bioinformatics Workshop", "url": "https://example.com/training", "type": "TrainingMaterial"}
        ]
    })
    link_decision = json.dumps({"follow": []})
    extraction = json.dumps({
        "@context": {"@vocab": "https://schema.org/", "dct": "http://purl.org/dc/terms/"},
        "@type": "LearningResource",
        "@id": "https://example.com/training",
        "name": "Bioinformatics Workshop",
        "description": "An introduction to bioinformatics tools.",
        "keywords": ["bioinformatics", "workshop"],
        "dct:conformsTo": {"@id": "https://bioschemas.org/profiles/TrainingMaterial/1.0-RELEASE"},
    })
    review = extraction  # reviewed version same as extracted

    client = MockLLMClient([discovery, link_decision, extraction, review])

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
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True)

    page_html = "<html><body><h1>Workshop</h1></body></html>"
    mock_response = _make_response(page_html)
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: mock_response)

    discovery = json.dumps({
        "training_items": [
            {"title": "Workshop", "url": "https://example.com/workshop", "type": "TrainingMaterial"}
        ]
    })
    # No links on page → no link_decision call
    # Extraction missing required fields
    bad_extraction = json.dumps({
        "@context": "https://schema.org",
        "@type": "LearningResource",
    })
    # Review doesn't fix it
    reviewed_still_bad = bad_extraction
    # Fix call returns a valid object
    fixed = json.dumps({
        "@context": {"@vocab": "https://schema.org/", "dct": "http://purl.org/dc/terms/"},
        "@type": "LearningResource",
        "@id": "https://example.com/workshop",
        "name": "Workshop",
        "description": "A hands-on workshop.",
        "keywords": ["workshop"],
        "dct:conformsTo": {"@id": "https://bioschemas.org/profiles/TrainingMaterial/1.0-RELEASE"},
    })

    client = MockLLMClient([discovery, bad_extraction, reviewed_still_bad, fixed])

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
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True)

    page_html = "<html><body><h1>Course</h1></body></html>"
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: _make_response(page_html))

    discovery = json.dumps({
        "training_items": [
            {"title": "Course", "url": "https://example.com/course", "type": "TrainingMaterial"}
        ]
    })
    # No links on page → no link_decision call
    # Missing dct:conformsTo and using simple @context
    extraction = json.dumps({
        "@context": "https://schema.org",
        "@type": "LearningResource",
        "name": "Course",
        "description": "A great course.",
        "keywords": ["course"],
    })
    review = extraction

    client = MockLLMClient([discovery, extraction, review])

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
    monkeypatch.setattr("urllib.robotparser.RobotFileParser.can_fetch", lambda self, ua, url: True)

    mock_response = _make_response(status_code=403)
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: mock_response)

    agent = BioschemasExtractorAgent()
    with pytest.raises(AccessDeniedError):
        agent.run(
            url="https://example.com/protected",
            llm_client=MockLLMClient([]),
        )
