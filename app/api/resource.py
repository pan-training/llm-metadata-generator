"""GET /metadata/single – returns a single Bioschemas JSON-LD object."""

import json
from typing import Any

from flask import Blueprint, Response, current_app, g, request
from flask.typing import ResponseReturnValue

from app.db.sqlite import get_db
from app.models.session import (
    append_log,
    create_session,
    get_active_session,
    get_latest_done_session,
    update_session,
)
from app.models.user import require_token

bp = Blueprint("resource", __name__)


def _run_extraction(
    app: Any,
    session_id: int,
    url: str,
    prompt: str | None,
    update_level: int,
    structural_summary: str | None,
) -> None:
    """Background task: run the extraction agent and store the result."""
    from app.agents import get_llm_client
    from app.agents.bioschemas import (
        AccessDeniedError,
        BioschemasExtractorAgent,
        NotTrainingContentError,
        compute_structural_summary,
    )

    with app.app_context():
        def log(msg: str) -> None:
            append_log(session_id, msg)

        try:
            update_session(session_id, "running")
            log(f"Starting extraction for {url}")

            llm_client = get_llm_client("default")
            agent = BioschemasExtractorAgent()
            result = agent.run(
                url=url,
                prompt=prompt,
                update_level=update_level,
                structural_summary=structural_summary,
                llm_client=llm_client,
                log_fn=log,
            )

            result_str = json.dumps(result)
            update_session(session_id, "done", result_json=result_str)
            log(f"Extraction complete: {len(result)} item(s)")

            # Update metadata_cache with structural summary
            summary = compute_structural_summary(result, url)
            import hashlib
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
                (url, content_hash, summary),
            )
            db.commit()

        except (NotTrainingContentError, AccessDeniedError) as exc:
            update_session(session_id, "error", log=str(exc))
        except Exception as exc:
            update_session(session_id, "error", log=f"Unexpected error: {exc}")


def _enqueue_extraction(
    url: str,
    prompt: str | None,
    session_id: int,
) -> None:
    """Determine update level and enqueue the background extraction job."""
    app = current_app._get_current_object()  # type: ignore[attr-defined]

    db = get_db()
    cache_row = db.execute(
        "SELECT content_hash, structural_summary FROM metadata_cache WHERE url = ?",
        (url,),
    ).fetchone()

    if cache_row is None:
        update_level = 2
        structural_summary = None
    else:
        update_level = 1
        structural_summary = cache_row["structural_summary"]

    scheduler = current_app.extensions.get("scheduler")
    if scheduler is not None:
        scheduler.add_job(
            func=_run_extraction,
            trigger="date",
            kwargs={
                "app": app,
                "session_id": session_id,
                "url": url,
                "prompt": prompt,
                "update_level": update_level,
                "structural_summary": structural_summary,
            },
        )


@bp.get("/metadata/single")
@require_token
def get_single() -> ResponseReturnValue:
    """Return a single Bioschemas JSON-LD object.

    Query params:
      url    – required; the source URL to extract from
      prompt – optional; additional instructions for the extraction agent

    Returns {} if no result yet.
    Returns the single item if result list has exactly 1 item.
    Returns 400 if result has multiple items (use /metadata for collections).
    """
    url = request.args.get("url", "").strip()
    if not url:
        return Response("Missing required query parameter: url", status=400)

    prompt = request.args.get("prompt") or None
    user = g.current_user

    # Return latest done result if available
    done_session = get_latest_done_session(user.id, url)
    if done_session and done_session.result_json:
        try:
            result_list: list[Any] = json.loads(done_session.result_json)
        except (json.JSONDecodeError, TypeError):
            result_list = []

        if len(result_list) == 1:
            return Response(
                json.dumps(result_list[0]),
                status=200,
                mimetype="application/ld+json",
            )
        elif len(result_list) > 1:
            return Response(
                "Multiple training content found. Use /metadata for collections.",
                status=400,
            )

    # Enqueue a new job if no active session exists
    active = get_active_session(user.id, url)
    if active is None:
        new_session = create_session(user.id, url)
        _enqueue_extraction(url, prompt, new_session.id)

    return Response(
        json.dumps({}),
        status=200,
        mimetype="application/ld+json",
    )
