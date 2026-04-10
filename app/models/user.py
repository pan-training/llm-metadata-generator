"""User model and Bearer-token authentication helpers.

Authentication is token-only: there are no usernames or passwords.
Tokens are generated with :func:`secrets.token_urlsafe` (URL-safe,
cryptographically strong random bytes).  Only the **SHA-256 hash** of
each token is persisted in the ``users`` table – the plaintext is never
stored, so a database leak does not expose usable credentials.  The
``@require_token`` decorator validates the ``Authorization: Bearer
<token>`` header on incoming requests.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

from flask import abort, g, request
from flask.typing import ResponseReturnValue

from app.db.sqlite import get_db

# Pattern that matches a SHA-256 hex digest (exactly 64 lowercase hex chars).
_SHA256_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")


def _hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of *token*.

    This is the value stored in the ``users.token_hash`` column.
    """
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class User:
    """Represents a row in the ``users`` table.

    ``token`` holds the plaintext Bearer token.  It is populated whenever
    the raw token is known (immediately after creation, or after a
    successful :func:`get_user_by_token` lookup).  It is ``None`` when the
    user record is loaded without knowing the plaintext (e.g. via
    :func:`list_users`).

    ``token_hash`` is the SHA-256 hex digest that is stored in the database
    and is always available.
    """

    id: int
    token: str | None  # plaintext; None when raw token is not known
    token_hash: str    # SHA-256 hex digest; always stored in DB
    created_at: str
    is_admin: bool


def get_user_by_token(token: str) -> "User | None":
    """Look up a user by their plaintext Bearer token.

    Hashes *token* before querying so the plaintext is never sent to the
    database layer.

    Returns the :class:`User` (with ``token`` set to the plaintext) if
    found, or ``None`` if the token is unknown or has been revoked.
    """
    token_hash = _hash_token(token)
    db = get_db()
    row = db.execute(
        "SELECT id, token_hash, created_at, is_admin FROM users WHERE token_hash = ?",
        (token_hash,),
    ).fetchone()
    if row is None:
        return None
    return User(
        id=row["id"],
        token=token,
        token_hash=row["token_hash"],
        created_at=row["created_at"],
        is_admin=bool(row["is_admin"]),
    )


def create_user(is_admin: bool = False) -> "User":
    """Create a new user with a freshly generated token.

    A random plaintext token is generated, its SHA-256 hash is stored in
    the database, and the :class:`User` returned contains the plaintext
    token so the caller can hand it to the new user.  The plaintext is
    **not** retained after this call.

    Args:
        is_admin: When ``True`` the new user is granted admin privileges.

    Returns:
        The newly created :class:`User` (``token`` contains the plaintext).
    """
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    db = get_db()
    db.execute(
        "INSERT INTO users (token_hash, is_admin) VALUES (?, ?)",
        (token_hash, int(is_admin)),
    )
    db.commit()
    user = get_user_by_token(token)
    assert user is not None  # we just inserted this token_hash
    return user


def list_users() -> list["User"]:
    """Return all users ordered by id.

    The plaintext tokens are not stored in the database, so each returned
    :class:`User` has ``token=None``.

    Returns:
        A list of :class:`User` instances, oldest first.
    """
    db = get_db()
    rows = db.execute(
        "SELECT id, token_hash, created_at, is_admin FROM users ORDER BY id"
    ).fetchall()
    return [
        User(
            id=row["id"],
            token=None,
            token_hash=row["token_hash"],
            created_at=row["created_at"],
            is_admin=bool(row["is_admin"]),
        )
        for row in rows
    ]


def delete_user(token: str) -> bool:
    """Delete the user identified by their plaintext Bearer token.

    Args:
        token: The raw Bearer token (will be hashed before the DB query).

    Returns:
        ``True`` if a user was deleted, ``False`` if the token was not found.
    """
    return delete_user_by_hash(_hash_token(token))


def delete_user_by_hash(token_hash: str) -> bool:
    """Delete the user identified by their token hash.

    Args:
        token_hash: The SHA-256 hex digest of the Bearer token.

    Returns:
        ``True`` if a user was deleted, ``False`` if the hash was not found.
    """
    db = get_db()
    result = db.execute("DELETE FROM users WHERE token_hash = ?", (token_hash,))
    db.commit()
    return bool(result.rowcount)


def revoke_user(token_or_hash: str) -> bool:
    """Delete the user identified by a plaintext token or its SHA-256 hash.

    The argument is interpreted as a token hash when it is exactly 64
    lowercase hexadecimal characters (the format produced by
    :func:`_hash_token`); otherwise it is treated as a plaintext token and
    hashed before the lookup.

    Args:
        token_or_hash: The raw Bearer token **or** its SHA-256 hex digest.

    Returns:
        ``True`` if a user was deleted, ``False`` if not found.
    """
    if _SHA256_RE.fullmatch(token_or_hash):
        return delete_user_by_hash(token_or_hash)
    return delete_user(token_or_hash)


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
