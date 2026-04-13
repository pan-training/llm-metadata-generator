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

    The plaintext Bearer token is **never** stored here (or anywhere on
    disk).  Only its SHA-256 hex digest (``token_hash``) is persisted and
    carried by this object.
    """

    id: int
    token_hash: str  # SHA-256 hex digest; always stored in DB
    created_at: str
    is_admin: bool


def get_user_by_token(token: str) -> "User | None":
    """Look up a user by their plaintext Bearer token.

    Hashes *token* before querying so the plaintext is never sent to the
    database layer.

    Returns the :class:`User` if found, or ``None`` if the token is unknown
    or has been revoked.
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
        token_hash=row["token_hash"],
        created_at=row["created_at"],
        is_admin=bool(row["is_admin"]),
    )


def create_user(is_admin: bool = False) -> "tuple[User, str]":
    """Create a new user with a freshly generated token.

    A random plaintext token is generated and its SHA-256 hash is stored in
    the database.  The plaintext token is returned alongside the
    :class:`User` so the caller can hand it to the new user.  It is
    **not** stored anywhere after this call.

    Args:
        is_admin: When ``True`` the new user is granted admin privileges.

    Returns:
        A ``(user, token)`` tuple.  *user* is the newly created
        :class:`User`; *token* is the one-time plaintext Bearer token that
        must be shown to the user immediately.
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
    return user, token


def list_users() -> list["User"]:
    """Return all users ordered by id.

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


def delete_user_by_id(user_id: int) -> bool:
    """Delete the user identified by their numeric database id.

    Args:
        user_id: The primary key of the user row.

    Returns:
        ``True`` if a user was deleted, ``False`` if the id was not found.
    """
    db = get_db()
    result = db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return bool(result.rowcount)


def revoke_user(identifier: "str | int") -> bool:
    """Delete the user identified by a plaintext token, token hash, or user id.

    *identifier* is interpreted as follows (in order):

    * **int** or **numeric string** – treated as a database user id.
    * **64-char lowercase hex string** – treated as a SHA-256 token hash.
    * **anything else** – treated as a plaintext Bearer token and hashed
      before the lookup.

    Args:
        identifier: The raw Bearer token, its SHA-256 hex digest, the
            user id as an integer, or the user id as a decimal string.

    Returns:
        ``True`` if a user was deleted, ``False`` if not found.
    """
    if isinstance(identifier, int):
        return delete_user_by_id(identifier)
    if identifier.isdigit():
        return delete_user_by_id(int(identifier))
    if _SHA256_RE.fullmatch(identifier):
        return delete_user_by_hash(identifier)
    return delete_user(identifier)


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
