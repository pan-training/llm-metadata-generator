"""Structured agent logger – typed event hierarchy for BioschemasExtractorAgent.

Each agent run emits a sequence of typed events:
  - InfoEvent      – general progress messages
  - WarnEvent      – non-fatal warnings (skipped pages, partial failures)
  - LLMCallEvent   – one LLM API call (task, model, prompt, response, latency)
  - FetchEvent     – one HTTP GET (url, status_code, content_length)
  - ItemFoundEvent – a training item discovered during crawl
  - ValidationEvent – Bioschemas schema validation result for an extracted item

All event-emitting methods return the new event's integer ``id``, which can be
passed as ``parent`` to later calls to express parent–child relationships::

    logger = AgentLogger()
    phase_id = logger.info("Phase 1: crawl")
    page_id  = logger.info("Fetching https://…", parent=phase_id)
    logger.fetch(url="https://…", …, parent=page_id)

The session viewer renders children as collapsible sub-items under their
parent event, giving a tree-structured timeline where sub-details can be
hidden for a cleaner overview.

Usage::

    logger = AgentLogger()
    agent.run(url=url, llm_client=client, logger=logger)
    print(logger.to_json())
    print(logger.summary())
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Union

# Maximum characters stored for prompt/response previews in LLMCallEvent.
# Set large enough to capture the full instruction section of typical prompts.
PREVIEW_LENGTH = 4000

# Maximum characters stored for the chunk/content field in LLMCallEvent.
# Page content can be very long; we cap it separately from the instruction.
CHUNK_PREVIEW_LENGTH = 8000


# ---------------------------------------------------------------------------
# Event dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InfoEvent:
    """General progress/status message."""

    message: str
    id: int = 0
    parent_id: int | None = None
    timestamp: float = field(default_factory=time.time)
    type: str = "info"


@dataclass
class WarnEvent:
    """Non-fatal warning (skipped page, transient error, …)."""

    message: str
    id: int = 0
    parent_id: int | None = None
    timestamp: float = field(default_factory=time.time)
    type: str = "warn"


@dataclass
class LLMCallEvent:
    """One LLM completion call with prompt preview, response preview, and latency.

    The optional ``chunk`` field holds the content/page-text that was submitted
    for analysis, stored separately from the instruction part of the prompt so
    that the viewer can display them in distinct panels.
    """

    task: str
    model: str
    prompt_preview: str
    response_preview: str
    latency_ms: float
    chunk: str = ""
    id: int = 0
    parent_id: int | None = None
    timestamp: float = field(default_factory=time.time)
    type: str = "llm_call"


@dataclass
class FetchEvent:
    """One HTTP GET request result."""

    url: str
    status_code: int
    content_length: int
    id: int = 0
    parent_id: int | None = None
    timestamp: float = field(default_factory=time.time)
    type: str = "fetch"


@dataclass
class ItemFoundEvent:
    """A training item discovered during the crawl phase."""

    title: str
    url: str
    item_type: str
    id: int = 0
    parent_id: int | None = None
    timestamp: float = field(default_factory=time.time)
    type: str = "item_found"


@dataclass
class ValidationEvent:
    """Bioschemas JSON schema validation result for one extracted item."""

    item_name: str
    errors: list[str]
    passed: bool
    id: int = 0
    parent_id: int | None = None
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

    All event-emitting methods return the new event's ``id`` (an ``int``)
    so callers can pass it as ``parent`` to child events.  The session viewer
    renders children as collapsible sub-items, giving a clean top-level view
    with details available on demand.

    Args:
        on_event: Optional callback invoked immediately after each event is
            recorded.  Use this to stream events to a console or file in
            real-time instead of processing them in bulk after the run.
    """

    def __init__(self, on_event: Callable[[AgentEvent], None] | None = None) -> None:
        self._events: list[AgentEvent] = []
        self._counter: int = 0
        #: Optional callback invoked after each event is recorded.  Assign or
        #: replace at any time; set to ``None`` to disable.
        self.on_event = on_event

    def _record(self, ev: AgentEvent) -> int:
        """Append *ev* to the internal list, fire the callback, return its id."""
        self._events.append(ev)
        if self.on_event is not None:
            self.on_event(ev)
        return ev.id

    def _next_id(self) -> int:
        self._counter += 1
        return self._counter

    # -- Logging methods -------------------------------------------------------

    def info(self, message: str, parent: int | None = None) -> int:
        """Emit an informational progress message.

        Returns the new event's ``id`` for use as ``parent`` by child events.
        """
        ev = InfoEvent(message=message, id=self._next_id(), parent_id=parent)
        return self._record(ev)

    def warn(self, message: str, parent: int | None = None) -> int:
        """Emit a non-fatal warning.

        Returns the new event's ``id`` for use as ``parent`` by child events.
        """
        ev = WarnEvent(message=message, id=self._next_id(), parent_id=parent)
        return self._record(ev)

    def llm_call(
        self,
        *,
        task: str,
        model: str,
        prompt: str,
        response: str,
        latency_ms: float,
        chunk: str = "",
        parent: int | None = None,
    ) -> int:
        """Emit a record of one LLM completion call.

        Args:
            task: Short task identifier (e.g. ``"content_relevance"``).
            model: Model name used for the call.
            prompt: The full instruction/system prompt text.  Capped to
                ``PREVIEW_LENGTH`` characters for storage.
            response: The raw response text.  Capped to ``PREVIEW_LENGTH``.
            latency_ms: Wall-clock time for the API call in milliseconds.
            chunk: The content/page-text submitted for analysis, stored
                separately from the instruction prompt so the viewer can
                display them in distinct panels.  Capped to
                ``CHUNK_PREVIEW_LENGTH`` characters.
            parent: Parent event ID for tree nesting.

        Returns the new event's ``id``.
        """
        ev = LLMCallEvent(
            task=task,
            model=model,
            prompt_preview=prompt[:PREVIEW_LENGTH],
            response_preview=response[:PREVIEW_LENGTH],
            chunk=chunk[:CHUNK_PREVIEW_LENGTH],
            latency_ms=round(latency_ms, 1),
            id=self._next_id(),
            parent_id=parent,
        )
        return self._record(ev)

    def fetch(
        self,
        url: str,
        status_code: int,
        content_length: int,
        parent: int | None = None,
    ) -> int:
        """Emit a record of one HTTP fetch.

        Returns the new event's ``id``.
        """
        ev = FetchEvent(
            url=url,
            status_code=status_code,
            content_length=content_length,
            id=self._next_id(),
            parent_id=parent,
        )
        return self._record(ev)

    def item_found(
        self,
        title: str,
        url: str,
        item_type: str,
        parent: int | None = None,
    ) -> int:
        """Emit a record of a newly discovered training item.

        Returns the new event's ``id``.
        """
        ev = ItemFoundEvent(
            title=title, url=url, item_type=item_type, id=self._next_id(), parent_id=parent
        )
        return self._record(ev)

    def validation(
        self,
        item_name: str,
        errors: list[str],
        passed: bool,
        parent: int | None = None,
    ) -> int:
        """Emit a schema validation result for one extracted item.

        Returns the new event's ``id``.
        """
        ev = ValidationEvent(
            item_name=item_name,
            errors=errors,
            passed=passed,
            id=self._next_id(),
            parent_id=parent,
        )
        return self._record(ev)

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
