"""Tests for ontology indexing/search helpers and admin routes."""

from __future__ import annotations

import os
import sqlite3
import tempfile

from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.db.sqlite import get_db, init_db, upsert_missing_ontology_term, vector_search


def _set_admin_session(client: FlaskClient, user_id: int = 1) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["is_admin"] = True


def _insert_active_ontology(db: sqlite3.Connection) -> None:
    cursor = db.execute(
        "INSERT INTO ontology_sources (name, description) VALUES (?, ?)",
        ("EDAM", "EDAM ontology"),
    )
    assert cursor.lastrowid is not None
    source_id = int(cursor.lastrowid)
    version_cursor = db.execute(
        "INSERT INTO ontology_index_versions (source_id, embedding_model, status, is_active)"
        " VALUES (?, ?, 'ready', 1)",
        (source_id, "test-embed"),
    )
    assert version_cursor.lastrowid is not None
    version_id = int(version_cursor.lastrowid)
    db.execute(
        "UPDATE ontology_sources SET active_version_id = ? WHERE id = ?",
        (version_id, source_id),
    )
    db.execute(
        "INSERT INTO ontology_terms (source_id, version_id, label, description, uri, ontology_name, embedding_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            source_id,
            version_id,
            "Sequence analysis",
            "EDAM sequence analysis topic",
            "http://edamontology.org/topic_0080",
            "EDAM",
            "[1.0, 0.0]",
        ),
    )
    db.execute(
        "INSERT INTO ontology_terms (source_id, version_id, label, description, uri, ontology_name, embedding_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            source_id,
            version_id,
            "Proteomics",
            "EDAM proteomics topic",
            "http://edamontology.org/topic_0121",
            "EDAM",
            "[0.0, 1.0]",
        ),
    )
    db.commit()


def _make_app() -> Flask:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    app = create_app({"TESTING": True, "DATABASE_URL": db_path})
    app.config["_TEST_DB_PATH"] = db_path
    return app


def _cleanup_app(app: Flask) -> None:
    db_path = app.config.get("_TEST_DB_PATH")
    if isinstance(db_path, str) and db_path and os.path.exists(db_path):
        os.unlink(db_path)


def _client_fixture(app: Flask) -> FlaskClient:
    return app.test_client()


def test_vector_search_returns_most_similar_term() -> None:
    app = _make_app()
    try:
        with app.app_context():
            init_db()
            db = get_db()
            _insert_active_ontology(db)
            matches = vector_search([1.0, 0.0], top_k=2)
        assert len(matches) >= 1
        assert matches[0]["label"] == "Sequence analysis"
        if len(matches) > 1:
            assert matches[0]["score"] >= matches[1]["score"]
    finally:
        _cleanup_app(app)


def test_upsert_missing_ontology_term_reuses_existing_label() -> None:
    app = _make_app()
    try:
        with app.app_context():
            init_db()
            first_id = upsert_missing_ontology_term(
                label="single-cell analysis",
                description="Needed for workshop metadata",
                ontology_name="EDAM",
                suggested_source_url="https://example.com/edam",
                metadata_url="https://example.com/training/a",
            )
            second_id = upsert_missing_ontology_term(
                label="single-cell analysis",
                description="same term another page",
                ontology_name="EDAM",
                suggested_source_url="https://example.com/edam",
                metadata_url="https://example.com/training/b",
            )
            db = get_db()
            link_count = db.execute(
                "SELECT COUNT(*) FROM missing_ontology_term_links WHERE missing_term_id = ?",
                (first_id,),
            ).fetchone()[0]
        assert first_id == second_id
        assert int(link_count) == 2
    finally:
        _cleanup_app(app)


def test_admin_ontology_lookup_can_test_indexing(monkeypatch) -> None:
    app = _make_app()
    try:
        with app.app_context():
            init_db()
            db = get_db()
            _insert_active_ontology(db)
        client = _client_fixture(app)
        _set_admin_session(client)
        monkeypatch.setattr("app.admin.routes._embed_lookup_query", lambda _q, _c: [1.0, 0.0])
        response = client.post(
            "/admin/ontologies",
            data={"action": "lookup", "lookup_query": "sequence analysis"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Sequence analysis" in response.data
    finally:
        _cleanup_app(app)
