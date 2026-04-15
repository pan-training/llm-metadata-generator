"""GET /metadata/single – returns a single Bioschemas JSON-LD object."""

import json
from typing import Any

from flask import Blueprint, Response, g, request
from flask.typing import ResponseReturnValue

from app.api._extraction import enqueue_extraction_if_needed
from app.models.session import get_latest_done_session
from app.models.user import require_token

bp = Blueprint("resource", __name__)


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
    force_refresh = request.args.get("force_refresh", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    user = g.current_user
    if force_refresh and not user.is_admin:
        return Response("force_refresh is admin-only", status=403)

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
    enqueue_extraction_if_needed(url, prompt, user.id, force_refresh=force_refresh)

    return Response(
        json.dumps({}),
        status=200,
        mimetype="application/ld+json",
    )
