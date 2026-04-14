"""Structured agent logger – typed event hierarchy for BioschemasExtractorAgent.

Each agent run emits a sequence of typed events:
  - InfoEvent      – general progress messages
  - WarnEvent      – non-fatal warnings (skipped pages, partial failures)
  - LLMCallEvent   – one LLM API call (task, model, prompt, response, latency)
  - FetchEvent     – one HTTP GET (url, status_code, content_length)
  - ItemFoundEvent – a training item discovered during crawl
  - ValidationEvent – Bioschemas schema validation result for an extracted item

Usage in the agent::

    logger = AgentLogger()
    agent.run(url=url, llm_client=client, logger=logger)
    print(logger.to_json())
    print(logger.summary())

Backward compatibility – plain ``log_fn: Callable[[str], None]`` callers::

    logs: list[str] = []
    agent.run(url=url, llm_client=client, log_fn=logs.append)
    # logs receives the message string from every InfoEvent / WarnEvent.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Union

# Maximum characters stored for prompt/response previews in LLMCallEvent.
# The full text is deliberately capped so that the DB log column stays compact.
PREVIEW_LENGTH = 600


# ---------------------------------------------------------------------------
# Event dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InfoEvent:
    """General progress/status message."""

    message: str
    timestamp: float = field(default_factory=time.time)
    type: str = "info"


@dataclass
class WarnEvent:
    """Non-fatal warning (skipped page, transient error, …)."""

    message: str
    timestamp: float = field(default_factory=time.time)
    type: str = "warn"


@dataclass
class LLMCallEvent:
    """One LLM completion call with prompt preview, response preview, and latency."""

    task: str
    model: str
    prompt_preview: str
    response_preview: str
    latency_ms: float
    timestamp: float = field(default_factory=time.time)
    type: str = "llm_call"


@dataclass
class FetchEvent:
    """One HTTP GET request result."""

    url: str
    status_code: int
    content_length: int
    timestamp: float = field(default_factory=time.time)
    type: str = "fetch"


@dataclass
class ItemFoundEvent:
    """A training item discovered during the crawl phase."""

    title: str
    url: str
    item_type: str
    timestamp: float = field(default_factory=time.time)
    type: str = "item_found"


@dataclass
class ValidationEvent:
    """Bioschemas JSON schema validation result for one extracted item."""

    item_name: str
    errors: list[str]
    passed: bool
    timestamp: float = field(default_factory=time.time)
    type: str = "validation"


AgentEvent = Union[
    InfoEvent, WarnEvent, LLMCallEvent, FetchEvent, ItemFoundEvent, ValidationEvent
]


# ---------------------------------------------------------------------------
# AgentLogger
# ---------------------------------------------------------------------------


class AgentLogger:
    """Collects typed events from one agent run.

    Args:
        legacy_fn: Optional plain-string callback (``Callable[[str], None]``).
            When provided, every :meth:`info` and :meth:`warn` call also
            forwards the message text to *legacy_fn* so that existing callers
            using ``log_fn`` continue to work without any changes.
    """

    def __init__(self, legacy_fn: Callable[[str], None] | None = None) -> None:
        self._events: list[AgentEvent] = []
        self._legacy_fn = legacy_fn

    # -- Logging methods -------------------------------------------------------

    def info(self, message: str) -> None:
        """Emit an informational progress message."""
        self._events.append(InfoEvent(message=message))
        if self._legacy_fn:
            self._legacy_fn(message)

    def warn(self, message: str) -> None:
        """Emit a non-fatal warning."""
        self._events.append(WarnEvent(message=message))
        if self._legacy_fn:
            self._legacy_fn(f"WARNING: {message}")

    def llm_call(
        self,
        *,
        task: str,
        model: str,
        prompt: str,
        response: str,
        latency_ms: float,
    ) -> None:
        """Emit a record of one LLM completion call."""
        self._events.append(
            LLMCallEvent(
                task=task,
                model=model,
                prompt_preview=prompt[:PREVIEW_LENGTH],
                response_preview=response[:PREVIEW_LENGTH],
                latency_ms=round(latency_ms, 1),
            )
        )

    def fetch(self, url: str, status_code: int, content_length: int) -> None:
        """Emit a record of one HTTP fetch."""
        self._events.append(
            FetchEvent(url=url, status_code=status_code, content_length=content_length)
        )

    def item_found(self, title: str, url: str, item_type: str) -> None:
        """Emit a record of a newly discovered training item."""
        self._events.append(ItemFoundEvent(title=title, url=url, item_type=item_type))

    def validation(self, item_name: str, errors: list[str], passed: bool) -> None:
        """Emit a schema validation result for one extracted item."""
        self._events.append(
            ValidationEvent(item_name=item_name, errors=errors, passed=passed)
        )

    def set_legacy_fn(self, legacy_fn: Callable[[str], None] | None) -> None:
        """Set or replace the legacy plain-string callback.

        This is a public alternative to passing *legacy_fn* in the constructor,
        useful when a caller wants to attach a callback after the logger is created.
        """
        self._legacy_fn = legacy_fn

    # -- Read-only access ------------------------------------------------------

    @property
    def events(self) -> list[AgentEvent]:
        """Return a copy of the event list."""
        return list(self._events)

    # -- Serialisation ---------------------------------------------------------

    def to_json(self) -> str:
        """Serialise all events to a JSON array string."""
        return json.dumps([asdict(e) for e in self._events], ensure_ascii=False)

    # -- Statistics ------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a statistics dict summarising the run.

        Includes per-task LLM call counts and latencies, total fetch count,
        item-found count, and validation error count.
        """
        llm_events = [e for e in self._events if isinstance(e, LLMCallEvent)]
        fetch_events = [e for e in self._events if isinstance(e, FetchEvent)]
        item_events = [e for e in self._events if isinstance(e, ItemFoundEvent)]
        validation_events = [e for e in self._events if isinstance(e, ValidationEvent)]

        total_latency = sum(e.latency_ms for e in llm_events)
        by_task: dict[str, dict[str, Any]] = {}
        for e in llm_events:
            stats = by_task.setdefault(e.task, {"count": 0, "total_ms": 0.0})
            stats["count"] += 1
            stats["total_ms"] = round(stats["total_ms"] + e.latency_ms, 1)

        return {
            "llm_calls": len(llm_events),
            "total_llm_ms": round(total_latency, 1),
            "llm_by_task": by_task,
            "fetches": len(fetch_events),
            "items_found": len(item_events),
            "validations": len(validation_events),
            "validation_errors": sum(len(e.errors) for e in validation_events),
        }
