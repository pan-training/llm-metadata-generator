"""Shared background extraction job used by both the collection and single-resource endpoints."""

import hashlib
import json
import logging
import posixpath
import random
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from flask import Flask
from flask import current_app

from app.agents.logger import AgentLogger
from app.db.sqlite import get_db
from app.models.session import create_session, get_active_session, update_session

_LOGGER = logging.getLogger(__name__)
MAX_CACHED_ITEM_URLS = 200
MAX_HASH_CHECK_PAGES = 200


@dataclass(frozen=True)
class ExtractionPlan:
    """Decision for whether to skip, incrementally refresh, or fully refresh."""

    mode: Literal["no_update", "incremental", "full_refresh"]
    structural_summary: str | None
    site_content_hash: str | None


def _get_structural_summary(url: str) -> str | None:
    """Return the cached structural summary for *url*, if present."""
    db = get_db()
    cache_row = db.execute(
        "SELECT structural_summary FROM metadata_cache WHERE url = ?",
        (url,),
    ).fetchone()
    return cache_row["structural_summary"] if cache_row else None


def _get_cache_row(url: str) -> sqlite3.Row | None:
    """Return the cache row for *url* from metadata_cache, if present."""
    db = get_db()
    return db.execute(
        "SELECT content_hash, structural_summary FROM metadata_cache WHERE url = ?",
        (url,),
    ).fetchone()


def _normalize_probability(raw_probability: object) -> float:
    """Parse and clamp a probability config value into [0.0, 1.0]."""
    try:
        if isinstance(raw_probability, (str, int, float)):
            probability = float(raw_probability)
        else:
            probability = 0.01
    except (TypeError, ValueError):
        probability = 0.01
    return max(0.0, min(1.0, probability))


def _fetch_site_content_hash(url: str) -> str | None:
    """Fetch *url* and return a stable hash of its markdown-normalised content."""
    from app.agents.bioschemas import _fetch, _html_to_markdown

    try:
        response = _fetch(url)
    except Exception as exc:
        _LOGGER.warning("Could not fetch %s for hash check: %s", url, exc)
        return None

    if not response.ok:
        _LOGGER.warning(
            "Could not fetch %s for hash check: HTTP %s",
            url,
            response.status_code,
        )
        return None

    markdown, _ = _html_to_markdown(response.text, url)
    return hashlib.sha256(markdown.encode()).hexdigest()


def _load_crawled_page_hashes(structural_summary: str | None) -> dict[str, str]:
    """Return cached per-page hashes from a structural summary, if valid."""
    if not structural_summary:
        return {}
    try:
        summary = json.loads(structural_summary)
    except json.JSONDecodeError:
        return {}
    if not isinstance(summary, dict):
        return {}
    raw_hashes = summary.get("crawled_page_hashes")
    if not isinstance(raw_hashes, dict):
        return {}

    normalized: dict[str, str] = {}
    for page_url, page_hash in raw_hashes.items():
        if not isinstance(page_url, str) or not isinstance(page_hash, str):
            continue
        normalized[page_url] = page_hash
        if len(normalized) >= MAX_HASH_CHECK_PAGES:
            break
    return normalized


def _snapshot_content_hash(page_hashes: Mapping[str, str], root_hash: str | None) -> str | None:
    """Build a deterministic hash over crawled pages, with root hash fallback."""
    normalized_pairs = sorted((str(url), str(content_hash)) for url, content_hash in page_hashes.items())
    if not normalized_pairs:
        return root_hash

    payload = {
        "root_hash": root_hash,
        "pages": normalized_pairs,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _build_extraction_plan(url: str, force_refresh: bool = False) -> ExtractionPlan:
    """Decide whether to skip, incrementally refresh, or fully refresh *url*."""
    cache_row = _get_cache_row(url)
    cached_hash = str(cache_row["content_hash"]) if cache_row and cache_row["content_hash"] else None
    cached_summary = (
        str(cache_row["structural_summary"])
        if cache_row and cache_row["structural_summary"]
        else None
    )

    if force_refresh:
        return ExtractionPlan(
            mode="full_refresh",
            structural_summary=None,
            site_content_hash=_fetch_site_content_hash(url),
        )

    if not cached_hash:
        return ExtractionPlan(
            mode="full_refresh",
            structural_summary=None,
            site_content_hash=_fetch_site_content_hash(url),
        )

    current_hash = _fetch_site_content_hash(url)
    if current_hash is None:
        return ExtractionPlan(
            mode="incremental",
            structural_summary=cached_summary,
            site_content_hash=None,
        )

    cached_page_hashes = _load_crawled_page_hashes(cached_summary)
    current_page_hashes: dict[str, str] = {}
    for page_url in cached_page_hashes:
        page_hash = _fetch_site_content_hash(page_url)
        if page_hash is None:
            _LOGGER.warning(
                "Could not refresh hash for cached page %s while planning %s; falling back to incremental refresh",
                page_url,
                url,
            )
            return ExtractionPlan(
                mode="incremental",
                structural_summary=cached_summary,
                site_content_hash=None,
            )
        current_page_hashes[page_url] = page_hash

    current_snapshot_hash = _snapshot_content_hash(current_page_hashes, current_hash)

    full_refresh_probability = _normalize_probability(
        current_app.config.get("CRON_METADATA_FULL_REFRESH_PROBABILITY", 0.01)
    )
    if random.random() < full_refresh_probability:
        return ExtractionPlan(
            mode="full_refresh",
            structural_summary=None,
            site_content_hash=current_snapshot_hash,
        )

    if current_snapshot_hash == cached_hash:
        return ExtractionPlan(
            mode="no_update",
            structural_summary=cached_summary,
            site_content_hash=current_snapshot_hash,
        )

    # Backward compatibility: old cache entries may contain only the root-page hash.
    # For entries with no cached subpages, all(...) is intentionally vacuously true.
    if cached_hash == current_hash and all(
        current_page_hashes.get(page_url) == cached_page_hash
        for page_url, cached_page_hash in cached_page_hashes.items()
    ):
        return ExtractionPlan(
            mode="no_update",
            structural_summary=cached_summary,
            site_content_hash=current_snapshot_hash,
        )

    return ExtractionPlan(
        mode="incremental",
        structural_summary=cached_summary,
        site_content_hash=current_snapshot_hash,
    )


def _item_path_common_prefix(item_urls: list[str]) -> str:
    """Return a compact common path prefix for *item_urls*."""
    if not item_urls:
        return ""
    parsed_paths = [urlparse(item_url).path for item_url in item_urls if item_url]
    if not parsed_paths:
        return ""
    try:
        return posixpath.commonpath(parsed_paths)
    except ValueError:
        # Expected when URL paths do not share a common root beyond "/".
        return ""


def _build_structural_summary(
    *,
    source_url: str,
    previous_summary: str | None,
    result: list[dict[str, object]],
    crawled_page_hashes: dict[str, str],
    items_by_url: dict[str, dict[str, object]],
) -> str:
    """Merge extracted run data into a structural summary persisted in metadata_cache."""
    summary: dict[str, object] = {}
    if previous_summary:
        try:
            loaded = json.loads(previous_summary)
            if isinstance(loaded, dict):
                summary = loaded
        except json.JSONDecodeError:
            summary = {}

    item_urls = [
        str(item.get("url") or item.get("@id"))
        for item in result
        if item.get("url") or item.get("@id")
    ][:MAX_CACHED_ITEM_URLS]

    summary["source_url"] = source_url
    summary["source_domain"] = urlparse(source_url).netloc
    summary["last_extracted"] = datetime.now(timezone.utc).isoformat()
    summary["item_count"] = len(result)
    summary["item_urls"] = item_urls
    summary["item_url_common_prefix"] = _item_path_common_prefix(item_urls)
    summary["crawled_page_hashes"] = crawled_page_hashes
    summary["items_by_url"] = items_by_url
    if "last_semantic_tool_search_at" in summary:
        summary["last_semantic_tool_search_at"] = summary["last_semantic_tool_search_at"]
    # Placeholder: this field is set by semantic-tool search once that integration exists.

    return json.dumps(summary, ensure_ascii=False)


def run_extraction(
    app: Flask,
    session_id: int,
    url: str,
    prompt: str | None,
    structural_summary: str | None,
    site_content_hash: str | None = None,
) -> None:
    """Background task: run the extraction agent and store the result in the session."""
    from app.agents import get_llm_client
    from app.agents.bioschemas import (
        AccessDeniedError,
        BioschemasExtractorAgent,
        NotTrainingContentError,
        compute_site_structure_summary,
    )

    with app.app_context():
        logger = AgentLogger()

        try:
            update_session(session_id, "running", log=logger.to_json())
            logger.info(f"Starting extraction for {url}")

            llm_client = get_llm_client("default")

            # Phase 0: compute the structural summary if not already cached.
            # The structural summary is computed once and reused across runs.
            if structural_summary is None:
                logger.info("No structural summary cached; computing now (Phase 0) …")
                try:
                    structural_summary = compute_site_structure_summary(
                        url=url,
                        llm_client=llm_client,
                        logger=logger,
                    )
                    # Persist immediately so subsequent runs skip Phase 0.
                    db = get_db()
                    db.execute(
                        "INSERT INTO metadata_cache (url, structural_summary)"
                        " VALUES (?, ?)"
                        " ON CONFLICT(url) DO UPDATE SET"
                        "   structural_summary = excluded.structural_summary,"
                        "   updated_at = datetime('now')",
                        (url, structural_summary),
                    )
                    db.commit()
                    logger.info("Structural summary stored in cache")
                except (AccessDeniedError, Exception) as exc:
                    logger.warn(
                        f"Could not compute structural summary: {exc};"
                        " proceeding without it"
                    )
                    structural_summary = None
            else:
                logger.info("Using cached structural summary")

            agent = BioschemasExtractorAgent()
            result = agent.run(
                url=url,
                prompt=prompt,
                structural_summary=structural_summary,
                llm_client=llm_client,
                logger=logger,
            )

            result_str = json.dumps(result)
            update_session(session_id, "done", log=logger.to_json(), result_json=result_str)

            extracted_page_hashes: dict[str, str] = getattr(
                agent,
                "last_crawled_page_hashes",
                {},
            )
            latest_items_by_url: dict[str, dict[str, object]] = getattr(
                agent,
                "last_items_by_url",
                {},
            )
            updated_structural_summary = _build_structural_summary(
                source_url=url,
                previous_summary=structural_summary,
                result=result,
                crawled_page_hashes=extracted_page_hashes,
                items_by_url=latest_items_by_url,
            )

            # Update metadata_cache with the latest site-content hash and summary.
            content_hash = _snapshot_content_hash(extracted_page_hashes, site_content_hash)
            if content_hash is None:
                content_hash = hashlib.sha256(result_str.encode()).hexdigest()
            db = get_db()
            db.execute(
                "INSERT INTO metadata_cache (url, content_hash, structural_summary, last_crawled_at)"
                " VALUES (?, ?, ?, datetime('now'))"
                " ON CONFLICT(url) DO UPDATE SET"
                "   content_hash = excluded.content_hash,"
                "   structural_summary = excluded.structural_summary,"
                "   last_crawled_at = excluded.last_crawled_at,"
                "   updated_at = datetime('now')",
                (url, content_hash, updated_structural_summary),
            )
            db.commit()

        except (NotTrainingContentError, AccessDeniedError) as exc:
            logger.info(f"Extraction stopped: {exc}")
            update_session(session_id, "error", log=logger.to_json())
        except Exception as exc:
            logger.warn(f"Unexpected error: {exc}")
            update_session(session_id, "error", log=logger.to_json())


def enqueue_extraction_if_needed(
    url: str,
    prompt: str | None,
    user_id: int,
    force_refresh: bool = False,
) -> None:
    """Create a pending session and enqueue an extraction job if no active session exists.

    Does nothing if there is already a pending or running session for (user_id, url).
    In testing mode (no scheduler attached) the session is created but not executed.
    """
    active = get_active_session(user_id, url)
    if active is not None:
        return

    plan = _build_extraction_plan(url, force_refresh=force_refresh)
    if plan.mode == "no_update":
        _LOGGER.info("Skipping extraction for %s: unchanged content hash", url)
        return

    new_session = create_session(user_id, url)

    # _get_current_object() unwraps the Flask application proxy so the real
    # app instance can be passed to a background thread.  APScheduler jobs
    # run outside the request context, so passing the proxy directly would
    # fail; we need the concrete object.
    app = current_app._get_current_object()  # type: ignore[attr-defined]

    scheduler = current_app.extensions.get("scheduler")
    if scheduler is not None:
        scheduler.add_job(
            func=run_extraction,
            trigger="date",
            kwargs={
                "app": app,
                "session_id": new_session.id,
                "url": url,
                "prompt": prompt,
                "structural_summary": plan.structural_summary,
                "site_content_hash": plan.site_content_hash,
            },
        )


def trigger_extraction_now(
    app: Flask,
    user_id: int,
    url: str,
    prompt: str | None,
) -> int:
    """Create a session for ``(user_id, url)`` and execute extraction immediately.

    Args:
        app: Flask application object used to push an app context in the worker.
        user_id: Owner of the new session.
        url: Source URL to extract metadata from.
        prompt: Optional prompt override for the extractor.

    Returns:
        The id of the newly created session.
    """
    new_session = create_session(user_id, url)
    run_extraction(
        app=app,
        session_id=new_session.id,
        url=url,
        prompt=prompt,
        structural_summary=_get_structural_summary(url),
        site_content_hash=_fetch_site_content_hash(url),
    )
    return new_session.id


def run_pending_extractions(
    app: Flask,
    user_id: int | None = None,
    url: str | None = None,
) -> list[int]:
    """Run queued (pending) extraction sessions immediately and return successful ids.

    Sessions are processed in creation order. If one session fails before
    ``run_extraction`` can persist an error state, processing continues with the
    remaining queued sessions.
    """
    db = get_db()
    query = (
        "SELECT id, url FROM sessions"
        " WHERE status = 'pending'"
        " AND (? IS NULL OR user_id = ?)"
        " AND (? IS NULL OR url = ?)"
        " ORDER BY created_at ASC"
    )
    rows = db.execute(query, (user_id, user_id, url, url)).fetchall()

    executed_ids: list[int] = []
    for row in rows:
        session_id = int(row["id"])
        session_url = str(row["url"])
        try:
            run_extraction(
                app=app,
                session_id=session_id,
                url=session_url,
                prompt=None,
                structural_summary=_get_structural_summary(session_url),
                site_content_hash=_fetch_site_content_hash(session_url),
            )
            executed_ids.append(session_id)
        except Exception:
            _LOGGER.exception(
                "Failed to execute queued extraction session %s for %s",
                session_id,
                session_url,
            )

    return executed_ids
