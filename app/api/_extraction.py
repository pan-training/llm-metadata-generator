"""Shared background extraction job used by both the collection and single-resource endpoints."""

import hashlib
import json
from typing import Any

from flask import current_app

from app.agents.logger import AgentLogger
from app.db.sqlite import get_db
from app.models.session import create_session, get_active_session, update_session


def run_extraction(
    app: Any,
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
        compute_structural_summary,
    )

    with app.app_context():
        logger = AgentLogger()

        try:
            update_session(session_id, "running", log=logger.to_json())
            logger.info(f"Starting extraction for {url}")

            llm_client = get_llm_client("default")
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

            # Update metadata_cache with the new content hash and structural summary
            db = get_db()
            # Retrieve the crawled page hashes stored in the agent's run state
            # via the structural summary (the agent embeds them there).
            summary = compute_structural_summary(result, url)
            content_hash = hashlib.sha256(result_str.encode()).hexdigest()
            db.execute(
                "INSERT INTO metadata_cache (url, content_hash, structural_summary, last_crawled_at)"
                " VALUES (?, ?, ?, datetime('now'))"
                " ON CONFLICT(url) DO UPDATE SET"
                "   content_hash = excluded.content_hash,"
                "   structural_summary = excluded.structural_summary,"
                "   last_crawled_at = excluded.last_crawled_at,"
                "   updated_at = datetime('now')",
                (url, content_hash, summary),
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
    db = get_db()
    cache_row = db.execute(
        "SELECT structural_summary FROM metadata_cache WHERE url = ?",
        (url,),
    ).fetchone()

    structural_summary = cache_row["structural_summary"] if cache_row else None

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
                "structural_summary": structural_summary,
            },
        )
