"""Health-check and self-identification endpoints.

``GET /health`` – no authentication required; useful for liveness probes.
``GET /whoami`` – requires a valid Bearer token; returns the caller's
                  user info (without the token itself).
"""

from flask import Blueprint, g, jsonify
from flask.typing import ResponseReturnValue

from app.models.user import require_token

bp = Blueprint("health", __name__)


@bp.get("/health")
def health() -> ResponseReturnValue:
    """Return a simple liveness response (no authentication required)."""
    return jsonify({"status": "ok"})


@bp.get("/whoami")
@require_token
def whoami() -> ResponseReturnValue:
    """Return information about the authenticated caller.

    Requires ``Authorization: Bearer <token>`` header.
    Returns the user's ``id``, ``created_at``, and ``is_admin`` flag.
    The token itself is intentionally omitted from the response.
    """
    user = g.current_user
    return jsonify(
        {
            "id": user.id,
            "created_at": user.created_at,
            "is_admin": user.is_admin,
        }
    )
