import json
import math
import sqlite3
from pathlib import Path
from typing import Any
from typing import Sequence

from flask import current_app, g


def _load_sqlite_vector_extension(conn: sqlite3.Connection) -> None:
    """Best-effort sqlite-vector extension loading.

    The application keeps a pure-Python fallback in :func:`vector_search`,
    so failure to load the extension must never break startup/tests.
    """
    try:
        conn.enable_load_extension(True)
    except sqlite3.Error:
        return

    loaded = False
    try:
        # https://github.com/sqliteai/sqlite-vector (python package API)
        from sqlite_vector import load as sqlite_vector_load  # type: ignore[import-not-found]

        sqlite_vector_load(conn)
        loaded = True
    except Exception:
        loaded = False

    if not loaded:
        try:
            # Common alternative package name in some environments.
            from sqlite_vec import load as sqlite_vec_load  # type: ignore[import-not-found]

            sqlite_vec_load(conn)
            loaded = True
        except Exception:
            loaded = False

    if not loaded:
        # Try extension names commonly used by packaged sqlite-vector builds.
        for ext_name in ("vector0", "sqlite_vector", "sqlite_vec"):
            try:
                conn.load_extension(ext_name)
                loaded = True
                break
            except sqlite3.Error:
                continue

    current_app.config["SQLITE_VECTOR_AVAILABLE"] = loaded
    try:
        conn.enable_load_extension(False)
    except sqlite3.Error:
        pass


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
        _load_sqlite_vector_extension(conn)
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


def _parse_embedding(embedding_json: str | None) -> list[float]:
    """Parse a JSON embedding array into a float list."""
    if not embedding_json:
        return []
    try:
        parsed = json.loads(embedding_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    if any(not isinstance(value, (int, float)) for value in parsed):
        return []
    return [float(value) for value in parsed]


def _to_embedding_list(query_embedding: Sequence[float] | str) -> list[float]:
    """Normalize a query embedding input into a float list."""
    if isinstance(query_embedding, str):
        return _parse_embedding(query_embedding)
    return [float(value) for value in query_embedding]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity for equal-length vectors, else 0."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def vector_search(query_embedding: Sequence[float] | str, top_k: int = 8) -> list[dict[str, Any]]:
    """Return top-k ontology term candidates ordered by cosine similarity."""
    query_vector = _to_embedding_list(query_embedding)
    if not query_vector or top_k <= 0:
        return []

    db = get_db()
    rows = db.execute(
        "SELECT ot.id, ot.label, ot.description, ot.uri, ot.ontology_name, ot.embedding_json"
        " FROM ontology_terms ot"
        " INNER JOIN ontology_sources os ON os.active_version_id = ot.version_id"
        " ORDER BY ot.id"
    ).fetchall()

    ranked: list[dict[str, Any]] = []
    for row in rows:
        embedding = _parse_embedding(row["embedding_json"])
        score = _cosine_similarity(query_vector, embedding)
        if score <= 0:
            continue
        ranked.append(
            {
                "id": row["id"],
                "label": row["label"],
                "description": row["description"],
                "uri": row["uri"],
                "ontology_name": row["ontology_name"],
                "score": score,
            }
        )

    ranked.sort(key=lambda item: float(item["score"]), reverse=True)
    return ranked[:top_k]


def upsert_missing_ontology_term(
    *,
    label: str,
    description: str | None,
    ontology_name: str | None,
    suggested_source_url: str | None,
    metadata_url: str,
) -> int:
    """Insert/find a missing ontology term and link it to a metadata_cache row."""
    normalized_label = label.strip()
    if not normalized_label:
        raise ValueError("label must not be empty")

    db = get_db()
    existing = db.execute(
        "SELECT id FROM missing_ontology_terms"
        " WHERE lower(label) = lower(?)"
        "   AND coalesce(lower(ontology_name), '') = coalesce(lower(?), '')",
        (normalized_label, ontology_name),
    ).fetchone()
    if existing is None:
        cursor = db.execute(
            "INSERT INTO missing_ontology_terms (label, description, ontology_name, suggested_source_url)"
            " VALUES (?, ?, ?, ?)",
            (normalized_label, description, ontology_name, suggested_source_url),
        )
        assert cursor.lastrowid is not None
        missing_term_id = int(cursor.lastrowid)
    else:
        missing_term_id = int(existing["id"])
        db.execute(
            "UPDATE missing_ontology_terms"
            " SET description = coalesce(?, description),"
            "     suggested_source_url = coalesce(?, suggested_source_url),"
            "     updated_at = datetime('now')"
            " WHERE id = ?",
            (description, suggested_source_url, missing_term_id),
        )

    cache_row = db.execute(
        "SELECT id FROM metadata_cache WHERE url = ?",
        (metadata_url,),
    ).fetchone()
    if cache_row is None:
        cache_cursor = db.execute(
            "INSERT INTO metadata_cache (url) VALUES (?)",
            (metadata_url,),
        )
        assert cache_cursor.lastrowid is not None
        metadata_cache_id = int(cache_cursor.lastrowid)
    else:
        metadata_cache_id = int(cache_row["id"])

    db.execute(
        "INSERT OR IGNORE INTO missing_ontology_term_links (missing_term_id, metadata_cache_id)"
        " VALUES (?, ?)",
        (missing_term_id, metadata_cache_id),
    )
    db.commit()
    return missing_term_id


def get_app_setting(key: str) -> str | None:
    """Return application setting value or None if unset."""
    db = get_db()
    row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    value = row["value"]
    return str(value) if value is not None else None


def set_app_setting(key: str, value: str) -> None:
    """Upsert an application setting key/value pair."""
    db = get_db()
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')",
        (key, value),
    )
    db.commit()
