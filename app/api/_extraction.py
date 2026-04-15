"""Shared background extraction job used by both the collection and single-resource endpoints."""

import hashlib
import json
import logging

from flask import Flask
from flask import current_app

from app.agents.logger import AgentLogger
from app.db.sqlite import get_db
from app.models.session import create_session, get_active_session, update_session

_LOGGER = logging.getLogger(__name__)


def build_extraction_job_id(session_id: int) -> str:
    """Return a deterministic APScheduler id used for lookup/cancellation."""
    return f"session-extraction-{session_id}"


def _is_structured_log_empty(log_value: str | None) -> bool:
    """Return True when log text is empty or a JSON-encoded empty list."""
    if not log_value:
        return True
    try:
        parsed = json.loads(log_value)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, list) and len(parsed) == 0


def _get_structural_summary(url: str) -> str | None:
    """Return the cached structural summary for *url*, if present."""
    db = get_db()
    cache_row = db.execute(
        "SELECT structural_summary FROM metadata_cache WHERE url = ?",
        (url,),
    ).fetchone()
    return cache_row["structural_summary"] if cache_row else None


def run_extraction(
    app: Flask,
    session_id: int,
    url: str,
    prompt: str | None,
    structural_summary: str | None,
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
            logger.info(f"Starting extraction for {url}")
            update_session(session_id, "running", log=logger.to_json())

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

            # Update metadata_cache with the new content hash.
            content_hash = hashlib.sha256(result_str.encode()).hexdigest()
            db = get_db()
            db.execute(
                "INSERT INTO metadata_cache (url, content_hash, last_crawled_at)"
                " VALUES (?, ?, datetime('now'))"
                " ON CONFLICT(url) DO UPDATE SET"
                "   content_hash = excluded.content_hash,"
                "   last_crawled_at = excluded.last_crawled_at,"
                "   updated_at = datetime('now')",
                (url, content_hash),
            )
            db.commit()

        except (NotTrainingContentError, AccessDeniedError) as exc:
            logger.info(f"Extraction stopped: {exc}")
            update_session(session_id, "error", log=logger.to_json())
        except Exception as exc:
            logger.warn(f"Unexpected error: {exc}")
            update_session(session_id, "error", log=logger.to_json())


def enqueue_extraction_if_needed(url: str, prompt: str | None, user_id: int) -> None:
    """Create a pending session and enqueue an extraction job if no active session exists.

    Does nothing if there is already a pending or running session for (user_id, url).
    In testing mode (no scheduler attached) the session is created but not executed.
    """
    active = get_active_session(user_id, url)
    if active is not None:
        return

    new_session = create_session(user_id, url)

    # _get_current_object() unwraps the Flask application proxy so the real
    # app instance can be passed to a background thread.  APScheduler jobs
    # run outside the request context, so passing the proxy directly would
    # fail; we need the concrete object.
    app = current_app._get_current_object()  # type: ignore[attr-defined]

    # Retrieve structural_summary from metadata_cache for incremental runs.
    # Pass None (full refresh) if no previous crawl is recorded.
    structural_summary = _get_structural_summary(url)

    scheduler = current_app.extensions.get("scheduler")
    if scheduler is not None:
        scheduler.add_job(
            func=run_extraction,
            trigger="date",
            id=build_extraction_job_id(new_session.id),
            replace_existing=True,
            kwargs={
                "app": app,
                "session_id": new_session.id,
                "url": url,
                "prompt": prompt,
                "structural_summary": structural_summary,
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
        "SELECT id, url, status, log FROM sessions"
        " WHERE status IN ('pending', 'running')"
        " AND (? IS NULL OR user_id = ?)"
        " AND (? IS NULL OR url = ?)"
        " ORDER BY created_at ASC"
    )
    rows = db.execute(query, (user_id, user_id, url, url)).fetchall()

    executed_ids: list[int] = []
    for row in rows:
        status = str(row["status"])
        log_value = row["log"]
        if status == "running" and not _is_structured_log_empty(log_value):
            continue

        session_id = int(row["id"])
        session_url = str(row["url"])
        try:
            run_extraction(
                app=app,
                session_id=session_id,
                url=session_url,
                prompt=None,
                structural_summary=_get_structural_summary(session_url),
            )
            executed_ids.append(session_id)
        except Exception:
            _LOGGER.exception(
                "Failed to execute queued extraction session %s for %s",
                session_id,
                session_url,
            )

    return executed_ids
