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
from app.models.user import User, create_user, delete_user, delete_user_by_hash, delete_user_by_id, get_user_by_token, revoke_user


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
        user, token = create_user()
    assert isinstance(user, User)
    assert user.id is not None
    assert len(token) > 0
    assert user.is_admin is False


def test_create_user_token_not_on_user_object(app: Flask) -> None:
    """The User object must not carry the plaintext token."""
    with app.app_context():
        user, _token = create_user()
    assert not hasattr(user, "token")


def test_create_user_stores_hash_not_plaintext(app: Flask) -> None:
    """The plaintext token must not appear in the database; only its hash should."""
    with app.app_context():
        user, token = create_user()
        expected_hash = hashlib.sha256(token.encode()).hexdigest()
        assert user.token_hash == expected_hash
        # Verify that get_user_by_token uses the stored hash, not plaintext.
        found = get_user_by_token(token)
    assert found is not None
    assert found.token_hash == expected_hash


def test_create_admin_user(app: Flask) -> None:
    with app.app_context():
        user, _token = create_user(is_admin=True)
    assert user.is_admin is True


def test_create_user_token_is_unique(app: Flask) -> None:
    with app.app_context():
        _u1, t1 = create_user()
        _u2, t2 = create_user()
    assert t1 != t2


def test_get_user_by_token_found(app: Flask) -> None:
    with app.app_context():
        user, token = create_user()
        found = get_user_by_token(token)
    assert found is not None
    assert found.id == user.id


def test_get_user_by_token_not_found(app: Flask) -> None:
    with app.app_context():
        result = get_user_by_token("this-token-does-not-exist")
    assert result is None


def test_delete_user_returns_true_when_found(app: Flask) -> None:
    with app.app_context():
        _user, token = create_user()
        deleted = delete_user(token)
        still_there = get_user_by_token(token)
    assert deleted is True
    assert still_there is None


def test_delete_user_returns_false_when_not_found(app: Flask) -> None:
    with app.app_context():
        deleted = delete_user("nonexistent-token")
    assert deleted is False


def test_delete_user_by_hash(app: Flask) -> None:
    with app.app_context():
        user, token = create_user()
        deleted = delete_user_by_hash(user.token_hash)
        still_there = get_user_by_token(token)
    assert deleted is True
    assert still_there is None


def test_delete_user_by_hash_returns_false_when_not_found(app: Flask) -> None:
    with app.app_context():
        deleted = delete_user_by_hash("a" * 64)
    assert deleted is False


def test_delete_user_by_id(app: Flask) -> None:
    with app.app_context():
        user, token = create_user()
        deleted = delete_user_by_id(user.id)
        still_there = get_user_by_token(token)
    assert deleted is True
    assert still_there is None


def test_delete_user_by_id_returns_false_when_not_found(app: Flask) -> None:
    with app.app_context():
        deleted = delete_user_by_id(99999)
    assert deleted is False


def test_revoke_user_with_plaintext_token(app: Flask) -> None:
    with app.app_context():
        _user, token = create_user()
        revoked = revoke_user(token)
        still_there = get_user_by_token(token)
    assert revoked is True
    assert still_there is None


def test_revoke_user_with_hash(app: Flask) -> None:
    with app.app_context():
        user, token = create_user()
        revoked = revoke_user(user.token_hash)
        still_there = get_user_by_token(token)
    assert revoked is True
    assert still_there is None


def test_revoke_user_with_int_id(app: Flask) -> None:
    with app.app_context():
        user, token = create_user()
        revoked = revoke_user(user.id)
        still_there = get_user_by_token(token)
    assert revoked is True
    assert still_there is None


def test_revoke_user_with_string_id(app: Flask) -> None:
    with app.app_context():
        user, token = create_user()
        revoked = revoke_user(str(user.id))
        still_there = get_user_by_token(token)
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
        user, token = create_user()

    response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["id"] == user.id
    assert data["is_admin"] is False
    assert "created_at" in data
    # Token must NOT be exposed in the response.
    assert "token" not in data


def test_whoami_admin_flag_is_correct(app: Flask, client: FlaskClient) -> None:
    with app.app_context():
        _admin, token = create_user(is_admin=True)

    response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.get_json()["is_admin"] is True


# ---------------------------------------------------------------------------
# Integration-tests route (admin-only)
# ---------------------------------------------------------------------------


def test_integration_tests_requires_login(client: FlaskClient) -> None:
    """GET /integration-tests without a session redirects to login."""
    response = client.get("/integration-tests")
    assert response.status_code == 302
    assert "/sessions/login" in response.headers["Location"]


def test_integration_tests_requires_admin(app: Flask, client: FlaskClient) -> None:
    """Non-admin users are forbidden from /integration-tests."""
    with app.app_context():
        _user, token = create_user(is_admin=False)

    # Log in as non-admin
    resp = client.post("/sessions/login", data={"token": token})
    assert resp.status_code == 302

    response = client.get("/integration-tests")
    assert response.status_code == 403


def test_integration_tests_admin_can_access(app: Flask, client: FlaskClient) -> None:
    """Admin users can access /integration-tests."""
    with app.app_context():
        _admin, token = create_user(is_admin=True)

    # Log in as admin
    resp = client.post("/sessions/login", data={"token": token})
    assert resp.status_code == 302

    response = client.get("/integration-tests")
    assert response.status_code == 200
    assert b"Integration Test" in response.data
