import os
import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db() -> sqlite3.Connection:
    """Return the per-request SQLite connection, creating it if necessary."""
    if "db" not in g:
        db_path: str = current_app.config["DATABASE_URL"]
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(e=None) -> None:
    """Close the per-request SQLite connection (registered as a teardown)."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Create all tables defined in schema.sql."""
    db = get_db()
    schema_path = Path(__file__).parent / "schema.sql"
    with open(schema_path) as fh:
        db.executescript(fh.read())
    db.commit()
