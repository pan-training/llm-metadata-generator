import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db() -> sqlite3.Connection:
    """Return a SQLite connection for the current application context.

    Flask's ``g`` object (see note below) caches the connection so that only one
    is created per application-context lifetime.  For HTTP requests Flask
    pushes and pops an application context automatically, so each request gets
    its own connection.  For APScheduler background jobs you must push the
    context manually::

        with app.app_context():
            db = get_db()
            # … do work …
            # close_db() is called automatically on context teardown

    Note on ``g``: ``flask.g`` is a namespace object that Flask provides for
    the lifetime of the current application context.  It is the standard place
    to store per-context resources such as database connections because Flask
    tears it down (and calls any registered teardown functions) automatically
    when the context exits.
    """
    if "db" not in g:
        db_path: str = current_app.config["DATABASE_URL"]
        if db_path != ":memory:":
            # Ensure the parent directory exists before opening the file.
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(e=None) -> None:
    """Close the current application-context's SQLite connection (registered as a teardown)."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Create (or ensure the existence of) all tables defined in ``schema.sql``.

    Safe to call multiple times – every statement uses ``CREATE TABLE IF NOT
    EXISTS``, so repeated calls are idempotent.

    Call this once at application start-up via ``flask db init``.  It is **not**
    a migration tool: adding columns or renaming them requires a manual
    ``ALTER TABLE`` statement run outside this function.  There is intentionally
    no migration framework (no Alembic) – the schema is kept simple enough that
    plain SQL ``IF NOT EXISTS`` guards are sufficient.
    """
    db = get_db()
    schema_path = Path(__file__).parent / "schema.sql"
    db.executescript(schema_path.read_text())
    db.commit()
