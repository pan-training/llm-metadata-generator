"""Smoke tests for the Flask application factory and database setup."""

import pytest
from flask import Flask

from app import create_app


@pytest.fixture
def app():
    """Create a test application instance with an in-memory database."""
    return create_app({"TESTING": True, "DATABASE_URL": ":memory:"})


def test_create_app_returns_flask_instance(app):
    assert isinstance(app, Flask)


def test_testing_flag_is_set(app):
    assert app.config["TESTING"] is True


def test_database_url_override(app):
    assert app.config["DATABASE_URL"] == ":memory:"


def test_init_db_creates_expected_tables(app):
    """init_db() must create all four tables defined in schema.sql."""
    # app.app_context() pushes a Flask application context, which is required
    # by get_db() and init_db() because they rely on flask.g and current_app.
    # Flask pushes this context automatically for each HTTP request; here we
    # do it manually because there is no real request in a unit test.
    with app.app_context():
        from app.db.sqlite import get_db, init_db

        init_db()
        db = get_db()
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {row["name"] for row in rows}

    assert "users" in table_names
    assert "sessions" in table_names
    assert "metadata_cache" in table_names
    assert "semantic_tools" in table_names


def test_db_init_is_idempotent(app):
    """Calling init_db() twice must not raise (IF NOT EXISTS guards)."""
    with app.app_context():  # see comment in test_init_db_creates_expected_tables
        from app.db.sqlite import init_db

        init_db()
        init_db()  # second call must succeed silently
