"""Tests for Bearer-token authentication and related endpoints."""

import hashlib
import os
import tempfile
from collections.abc import Generator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.db.sqlite import init_db
from app.models.user import User, create_user, delete_user, delete_user_by_hash, get_user_by_token, revoke_user


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
    assert user.token is not None
    assert len(user.token) > 0
    assert user.is_admin is False


def test_create_user_stores_hash_not_plaintext(app: Flask) -> None:
    """The plaintext token must not appear in the database; only its hash should."""
    with app.app_context():
        user = create_user()
        assert user.token is not None
        expected_hash = hashlib.sha256(user.token.encode()).hexdigest()
        assert user.token_hash == expected_hash
        # Verify that get_user_by_token uses the stored hash, not plaintext.
        found = get_user_by_token(user.token)
    assert found is not None
    assert found.token_hash == expected_hash


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
        assert user.token is not None
        found = get_user_by_token(user.token)
    assert found is not None
    assert found.id == user.id


def test_get_user_by_token_not_found(app: Flask) -> None:
    with app.app_context():
        result = get_user_by_token("this-token-does-not-exist")
    assert result is None


def test_delete_user_returns_true_when_found(app: Flask) -> None:
    with app.app_context():
        user = create_user()
        assert user.token is not None
        deleted = delete_user(user.token)
        still_there = get_user_by_token(user.token)
    assert deleted is True
    assert still_there is None


def test_delete_user_returns_false_when_not_found(app: Flask) -> None:
    with app.app_context():
        deleted = delete_user("nonexistent-token")
    assert deleted is False


def test_delete_user_by_hash(app: Flask) -> None:
    with app.app_context():
        user = create_user()
        assert user.token is not None
        deleted = delete_user_by_hash(user.token_hash)
        still_there = get_user_by_token(user.token)
    assert deleted is True
    assert still_there is None


def test_delete_user_by_hash_returns_false_when_not_found(app: Flask) -> None:
    with app.app_context():
        deleted = delete_user_by_hash("a" * 64)
    assert deleted is False


def test_revoke_user_with_plaintext_token(app: Flask) -> None:
    with app.app_context():
        user = create_user()
        assert user.token is not None
        revoked = revoke_user(user.token)
        still_there = get_user_by_token(user.token)
    assert revoked is True
    assert still_there is None


def test_revoke_user_with_hash(app: Flask) -> None:
    with app.app_context():
        user = create_user()
        revoked = revoke_user(user.token_hash)
        assert user.token is not None
        still_there = get_user_by_token(user.token)
    assert revoked is True
    assert still_there is None


def test_revoke_user_returns_false_when_not_found(app: Flask) -> None:
    with app.app_context():
        revoked = revoke_user("nonexistent-token")
    assert revoked is False


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

    assert user.token is not None
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

    assert admin.token is not None
    response = client.get("/whoami", headers={"Authorization": f"Bearer {admin.token}"})
    assert response.status_code == 200
    assert response.get_json()["is_admin"] is True
