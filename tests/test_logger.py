"""Tests for AgentLogger – typed event hierarchy and statistics."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agents.logger import (
    AgentLogger,
    FetchEvent,
    InfoEvent,
    ItemFoundEvent,
    LLMCallEvent,
    ValidationEvent,
    WarnEvent,
)


# ---------------------------------------------------------------------------
# Basic event emission
# ---------------------------------------------------------------------------


def test_logger_info_adds_event() -> None:
    logger = AgentLogger()
    logger.info("hello")
    assert len(logger.events) == 1
    assert isinstance(logger.events[0], InfoEvent)
    assert logger.events[0].message == "hello"
    assert logger.events[0].type == "info"


def test_logger_warn_adds_event() -> None:
    logger = AgentLogger()
    logger.warn("something wrong")
    assert len(logger.events) == 1
    assert isinstance(logger.events[0], WarnEvent)
    assert logger.events[0].type == "warn"


def test_logger_fetch_adds_event() -> None:
    logger = AgentLogger()
    logger.fetch(url="https://example.com", status_code=200, content_length=1234)
    assert len(logger.events) == 1
    ev = logger.events[0]
    assert isinstance(ev, FetchEvent)
    assert ev.url == "https://example.com"
    assert ev.status_code == 200
    assert ev.content_length == 1234
    assert ev.type == "fetch"


def test_logger_item_found_adds_event() -> None:
    logger = AgentLogger()
    logger.item_found(title="Workshop", url="https://example.com/ws", item_type="CourseInstance")
    ev = logger.events[0]
    assert isinstance(ev, ItemFoundEvent)
    assert ev.title == "Workshop"
    assert ev.type == "item_found"


def test_logger_llm_call_adds_event() -> None:
    logger = AgentLogger()
    logger.llm_call(
        task="content_relevance",
        model="qwen2.5",
        prompt="Is this relevant?",
        response='{"relevant": true}',
        latency_ms=123.4,
    )
    ev = logger.events[0]
    assert isinstance(ev, LLMCallEvent)
    assert ev.task == "content_relevance"
    assert ev.model == "qwen2.5"
    assert ev.latency_ms == 123.4
    assert ev.type == "llm_call"


def test_logger_llm_call_truncates_prompt_preview() -> None:
    logger = AgentLogger()
    long_prompt = "x" * 1000
    logger.llm_call(
        task="test",
        model="m",
        prompt=long_prompt,
        response="ok",
        latency_ms=0,
    )
    ev = logger.events[0]
    assert isinstance(ev, LLMCallEvent)
    assert len(ev.prompt_preview) <= 600


def test_logger_validation_event_passed() -> None:
    logger = AgentLogger()
    logger.validation(item_name="Workshop", errors=[], passed=True)
    ev = logger.events[0]
    assert isinstance(ev, ValidationEvent)
    assert ev.passed is True
    assert ev.errors == []
    assert ev.type == "validation"


def test_logger_validation_event_failed() -> None:
    logger = AgentLogger()
    logger.validation(item_name="Workshop", errors=["name required", "keywords required"], passed=False)
    ev = logger.events[0]
    assert isinstance(ev, ValidationEvent)
    assert ev.passed is False
    assert len(ev.errors) == 2


# ---------------------------------------------------------------------------
# Legacy log_fn backward compatibility
# ---------------------------------------------------------------------------


def test_logger_legacy_fn_receives_info_messages() -> None:
    messages: list[str] = []
    logger = AgentLogger(legacy_fn=messages.append)
    logger.info("progress message")
    assert "progress message" in messages


def test_logger_legacy_fn_receives_warn_messages() -> None:
    messages: list[str] = []
    logger = AgentLogger(legacy_fn=messages.append)
    logger.warn("something bad")
    assert any("something bad" in m for m in messages)


def test_logger_legacy_fn_not_called_for_llm_or_fetch() -> None:
    messages: list[str] = []
    logger = AgentLogger(legacy_fn=messages.append)
    logger.fetch("https://example.com", 200, 100)
    logger.llm_call(task="t", model="m", prompt="p", response="r", latency_ms=1.0)
    # Only info/warn events are forwarded to legacy_fn
    assert len(messages) == 0


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------


def test_to_json_produces_valid_json() -> None:
    logger = AgentLogger()
    logger.info("step 1")
    logger.fetch("https://example.com", 200, 500)
    raw = logger.to_json()
    data = json.loads(raw)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["type"] == "info"
    assert data[1]["type"] == "fetch"


def test_to_json_empty_logger() -> None:
    logger = AgentLogger()
    data = json.loads(logger.to_json())
    assert data == []


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------


def test_summary_counts_llm_calls() -> None:
    logger = AgentLogger()
    logger.llm_call(task="content_relevance", model="m", prompt="p", response="r", latency_ms=100.0)
    logger.llm_call(task="content_relevance", model="m", prompt="p", response="r", latency_ms=200.0)
    logger.llm_call(task="metadata_analysis", model="m", prompt="p", response="r", latency_ms=50.0)
    stats = logger.summary()
    assert stats["llm_calls"] == 3
    assert stats["total_llm_ms"] == pytest.approx(350.0, abs=1.0)
    assert stats["llm_by_task"]["content_relevance"]["count"] == 2
    assert stats["llm_by_task"]["metadata_analysis"]["count"] == 1


def test_summary_counts_fetches_and_items() -> None:
    logger = AgentLogger()
    logger.fetch("https://a.com", 200, 100)
    logger.fetch("https://b.com", 200, 200)
    logger.item_found("Item A", "https://a.com/1", "TrainingMaterial")
    stats = logger.summary()
    assert stats["fetches"] == 2
    assert stats["items_found"] == 1


def test_summary_counts_validation_errors() -> None:
    logger = AgentLogger()
    logger.validation("Item A", errors=["err1", "err2"], passed=False)
    logger.validation("Item B", errors=[], passed=True)
    stats = logger.summary()
    assert stats["validations"] == 2
    assert stats["validation_errors"] == 2


def test_summary_empty_logger() -> None:
    logger = AgentLogger()
    stats = logger.summary()
    assert stats["llm_calls"] == 0
    assert stats["total_llm_ms"] == 0
    assert stats["fetches"] == 0
    assert stats["items_found"] == 0
