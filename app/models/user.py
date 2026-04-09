"""User model and Bearer-token authentication helpers.

Authentication is token-only: there are no usernames or passwords.
Tokens are generated with :func:`secrets.token_urlsafe` (URL-safe,
cryptographically strong random bytes) and stored directly in the
``users`` table.  The ``@require_token`` decorator validates the
``Authorization: Bearer <token>`` header on incoming requests.
"""

import secrets
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

from flask import abort, g, request
from flask.typing import ResponseReturnValue

from app.db.sqlite import get_db


@dataclass
class User:
    """Represents a row in the ``users`` table."""

    id: int
    token: str
    created_at: str
    is_admin: bool


def get_user_by_token(token: str) -> User | None:
    """Look up a user by their Bearer token.

    Returns the :class:`User` if found, or ``None`` if the token is
    unknown or has been revoked.
    """
    db = get_db()
    row = db.execute(
        "SELECT id, token, created_at, is_admin FROM users WHERE token = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    return User(
        id=row["id"],
        token=row["token"],
        created_at=row["created_at"],
        is_admin=bool(row["is_admin"]),
    )


def create_user(is_admin: bool = False) -> User:
    """Create a new user with a freshly generated token.

    Args:
        is_admin: When ``True`` the new user is granted admin privileges.

    Returns:
        The newly created :class:`User` (including its database ``id``).
    """
    token = secrets.token_urlsafe(32)
    db = get_db()
    db.execute(
        "INSERT INTO users (token, is_admin) VALUES (?, ?)",
        (token, int(is_admin)),
    )
    db.commit()
    user = get_user_by_token(token)
    assert user is not None  # we just inserted this token
    return user


def list_users() -> list["User"]:
    """Return all users ordered by id.

    Returns:
        A list of :class:`User` instances, oldest first.
    """
    db = get_db()
    rows = db.execute(
        "SELECT id, token, created_at, is_admin FROM users ORDER BY id"
    ).fetchall()
    return [
        User(
            id=row["id"],
            token=row["token"],
            created_at=row["created_at"],
            is_admin=bool(row["is_admin"]),
        )
        for row in rows
    ]


def require_token(f: Callable[..., ResponseReturnValue]) -> Callable[..., ResponseReturnValue]:
    """Decorator that enforces Bearer-token authentication.

    Reads the ``Authorization: Bearer <token>`` header, looks up the
    corresponding user, and stores the :class:`User` instance in
    ``flask.g.current_user`` for use by the decorated view function.

    Aborts with HTTP ``401`` if the header is missing, malformed, or
    contains an unknown/revoked token.  Tokens must **never** be passed
    as URL query parameters – this decorator only accepts them via the
    ``Authorization`` header.
    """

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> ResponseReturnValue:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            abort(
                401,
                description=(
                    "Missing or malformed Authorization header. "
                    "Expected format: Authorization: Bearer <token>"
                ),
                www_authenticate='Bearer realm="API"',
            )
        token = auth_header.removeprefix("Bearer ")
        user = get_user_by_token(token)
        if user is None:
            abort(401)
        g.current_user = user
        return f(*args, **kwargs)

    return decorated
