"""Tests for Bearer-token authentication and related endpoints."""

import os
import tempfile
from collections.abc import Generator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.db.sqlite import init_db
from app.models.user import User, create_user, get_user_by_token


@pytest.fixture
def app() -> Generator[Flask, None, None]:
    """Application backed by a temporary SQLite file that persists across requests.

    A named file (rather than ``:memory:``) is required here because
    SQLite in-memory databases are per-connection: once a request context
    closes its connection the tables disappear, so the next request would
    see an empty database.
    """
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    application = create_app({"TESTING": True, "DATABASE_URL": db_path})
    with application.app_context():
        init_db()
    yield application
    os.unlink(db_path)


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


# ---------------------------------------------------------------------------
# User model helpers
# ---------------------------------------------------------------------------


def test_create_user_returns_user(app: Flask) -> None:
    with app.app_context():
        user = create_user()
    assert isinstance(user, User)
    assert user.id is not None
    assert len(user.token) > 0
    assert user.is_admin is False


def test_create_admin_user(app: Flask) -> None:
    with app.app_context():
        user = create_user(is_admin=True)
    assert user.is_admin is True


def test_create_user_token_is_unique(app: Flask) -> None:
    with app.app_context():
        u1 = create_user()
        u2 = create_user()
    assert u1.token != u2.token


def test_get_user_by_token_found(app: Flask) -> None:
    with app.app_context():
        user = create_user()
        found = get_user_by_token(user.token)
    assert found is not None
    assert found.id == user.id


def test_get_user_by_token_not_found(app: Flask) -> None:
    with app.app_context():
        result = get_user_by_token("this-token-does-not-exist")
    assert result is None


# ---------------------------------------------------------------------------
# GET /health  (no authentication required)
# ---------------------------------------------------------------------------


def test_health_returns_200(client: FlaskClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_ok_json(client: FlaskClient) -> None:
    response = client.get("/health")
    assert response.get_json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /whoami  (Bearer-token authentication required)
# ---------------------------------------------------------------------------


def test_whoami_without_token_returns_401(client: FlaskClient) -> None:
    response = client.get("/whoami")
    assert response.status_code == 401


def test_whoami_with_invalid_token_returns_401(client: FlaskClient) -> None:
    response = client.get("/whoami", headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code == 401


def test_whoami_with_malformed_header_returns_401(client: FlaskClient) -> None:
    """Header present but does not start with 'Bearer ' must also yield 401."""
    response = client.get("/whoami", headers={"Authorization": "Token sometoken"})
    assert response.status_code == 401


def test_whoami_with_valid_token_returns_user_info(app: Flask, client: FlaskClient) -> None:
    with app.app_context():
        user = create_user()

    response = client.get("/whoami", headers={"Authorization": f"Bearer {user.token}"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["id"] == user.id
    assert data["is_admin"] is False
    assert "created_at" in data
    # Token must NOT be exposed in the response.
    assert "token" not in data


def test_whoami_admin_flag_is_correct(app: Flask, client: FlaskClient) -> None:
    with app.app_context():
        admin = create_user(is_admin=True)

    response = client.get("/whoami", headers={"Authorization": f"Bearer {admin.token}"})
    assert response.status_code == 200
    assert response.get_json()["is_admin"] is True
