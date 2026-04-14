"""GET /metadata – returns Bioschemas JSON-LD list for a training collection."""

import json
from typing import Any

from flask import Blueprint, Response, g, request
from flask.typing import ResponseReturnValue

from app.api._extraction import enqueue_extraction_if_needed
from app.models.session import get_latest_done_session
from app.models.user import require_token

bp = Blueprint("collection", __name__)


@bp.get("/metadata")
@require_token
def get_collection() -> ResponseReturnValue:
    """Return Bioschemas JSON-LD list.

    Query params:
      url    – required; the source URL to extract from
      prompt – optional; additional instructions for the extraction agent

    Flow (lazy generation):
      1. Return latest cached result immediately (empty list if none).
      2. If no active (pending/running) session exists for (user, url), enqueue a new one.
    """
    url = request.args.get("url", "").strip()
    if not url:
        return Response("Missing required query parameter: url", status=400)

    prompt = request.args.get("prompt") or None
    user = g.current_user

    # Return latest done result if available
    done_session = get_latest_done_session(user.id, url)
    result: list[Any] = []
    if done_session and done_session.result_json:
        try:
            result = json.loads(done_session.result_json)
        except (json.JSONDecodeError, TypeError):
            result = []

    # Enqueue a new job if no active session exists
    enqueue_extraction_if_needed(url, prompt, user.id)

    return Response(
        json.dumps(result),
        status=200,
        mimetype="application/ld+json",
    )

