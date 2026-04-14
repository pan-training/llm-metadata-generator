"""Session model for tracking background generation jobs."""

from dataclasses import dataclass

from app.db.sqlite import get_db


@dataclass
class Session:
    """Represents a row in the ``sessions`` table."""

    id: int
    user_id: int
    url: str
    status: str
    log: str | None
    result_json: str | None
    created_at: str
    updated_at: str


def _row_to_session(row: object) -> Session:
    return Session(
        id=row["id"],  # type: ignore[index]
        user_id=row["user_id"],  # type: ignore[index]
        url=row["url"],  # type: ignore[index]
        status=row["status"],  # type: ignore[index]
        log=row["log"],  # type: ignore[index]
        result_json=row["result_json"],  # type: ignore[index]
        created_at=row["created_at"],  # type: ignore[index]
        updated_at=row["updated_at"],  # type: ignore[index]
    )


def create_session(user_id: int, url: str) -> Session:
    """Create a new pending session for (user_id, url) and return it."""
    db = get_db()
    cursor = db.execute(
        "INSERT INTO sessions (user_id, url, status) VALUES (?, ?, 'pending')",
        (user_id, url),
    )
    db.commit()
    session_id = cursor.lastrowid
    assert session_id is not None
    result = get_session_by_id(session_id)
    assert result is not None
    return result


def get_session_by_id(session_id: int) -> Session | None:
    """Return the session with the given id, or None if not found."""
    db = get_db()
    row = db.execute(
        "SELECT id, user_id, url, status, log, result_json, created_at, updated_at"
        " FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_session(row)


def get_latest_done_session(user_id: int, url: str) -> Session | None:
    """Return the most recently completed session for (user_id, url), or None."""
    db = get_db()
    row = db.execute(
        "SELECT id, user_id, url, status, log, result_json, created_at, updated_at"
        " FROM sessions"
        " WHERE user_id = ? AND url = ? AND status = 'done'"
        " ORDER BY updated_at DESC LIMIT 1",
        (user_id, url),
    ).fetchone()
    if row is None:
        return None
    return _row_to_session(row)


def get_active_session(user_id: int, url: str) -> Session | None:
    """Return a pending or running session for (user_id, url), or None."""
    db = get_db()
    row = db.execute(
        "SELECT id, user_id, url, status, log, result_json, created_at, updated_at"
        " FROM sessions"
        " WHERE user_id = ? AND url = ? AND status IN ('pending', 'running')"
        " ORDER BY created_at DESC LIMIT 1",
        (user_id, url),
    ).fetchone()
    if row is None:
        return None
    return _row_to_session(row)


def update_session(
    session_id: int,
    status: str,
    log: str | None = None,
    result_json: str | None = None,
) -> None:
    """Update the status (and optionally log/result_json) of a session."""
    db = get_db()
    db.execute(
        "UPDATE sessions"
        " SET status = ?, log = COALESCE(?, log), result_json = COALESCE(?, result_json),"
        "     updated_at = datetime('now')"
        " WHERE id = ?",
        (status, log, result_json, session_id),
    )
    db.commit()


def append_log(session_id: int, message: str) -> None:
    """Append a line to the session log (creates log if it doesn't exist)."""
    db = get_db()
    db.execute(
        "UPDATE sessions"
        " SET log = CASE WHEN log IS NULL THEN ? ELSE log || char(10) || ? END,"
        "     updated_at = datetime('now')"
        " WHERE id = ?",
        (message, message, session_id),
    )
    db.commit()


def get_sessions_for_user(user_id: int) -> list[Session]:
    """Return all sessions for the given user, most recent first."""
    db = get_db()
    rows = db.execute(
        "SELECT id, user_id, url, status, log, result_json, created_at, updated_at"
        " FROM sessions WHERE user_id = ?"
        " ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [_row_to_session(row) for row in rows]
