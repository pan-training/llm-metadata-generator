"""Tests for the collection and resource API endpoints and session viewer."""

import json
import os
import tempfile
from collections.abc import Generator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.db.sqlite import init_db
from app.models.user import create_user
from app.models.session import create_session, get_session_by_id, update_session


@pytest.fixture
def app() -> Generator[Flask, None, None]:
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


@pytest.fixture(autouse=True)
def disable_site_hash_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent endpoint tests from performing live network fetches."""
    monkeypatch.setattr(
        "app.api._extraction._fetch_site_content_hash",
        lambda _url: "test-site-hash",
    )


@pytest.fixture
def auth_header(app: Flask) -> dict[str, str]:
    with app.app_context():
        _user, token = create_user()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_and_token(app: Flask) -> tuple[int, str]:
    with app.app_context():
        user, token = create_user()
    return user.id, token


# ---------------------------------------------------------------------------
# GET /metadata
# ---------------------------------------------------------------------------


def test_get_collection_without_auth_returns_401(client: FlaskClient) -> None:
    response = client.get("/metadata?url=https://example.com/training")
    assert response.status_code == 401


def test_get_collection_missing_url_returns_400(
    client: FlaskClient, auth_header: dict[str, str]
) -> None:
    response = client.get("/metadata", headers=auth_header)
    assert response.status_code == 400


def test_get_collection_returns_empty_list_initially(
    app: Flask, client: FlaskClient
) -> None:
    with app.app_context():
        _user, token = create_user()
    response = client.get(
        "/metadata?url=https://example.com/training",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data == []


def test_get_collection_content_type_is_json_ld(
    app: Flask, client: FlaskClient
) -> None:
    with app.app_context():
        _user, token = create_user()
    response = client.get(
        "/metadata?url=https://example.com/training",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert "application/ld+json" in response.content_type


def test_get_collection_force_refresh_is_admin_only(
    app: Flask, client: FlaskClient
) -> None:
    with app.app_context():
        _user, token = create_user()

    response = client.get(
        "/metadata?url=https://example.com/training&force_refresh=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_get_collection_force_refresh_allowed_for_admin(
    app: Flask, client: FlaskClient
) -> None:
    with app.app_context():
        _user, token = create_user(is_admin=True)

    response = client.get(
        "/metadata?url=https://example.com/training&force_refresh=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_get_collection_enqueues_session(app: Flask, client: FlaskClient) -> None:
    """After a call, a pending session must exist for (user, url)."""
    with app.app_context():
        user, token = create_user()

    client.get(
        "/metadata?url=https://example.com/training",
        headers={"Authorization": f"Bearer {token}"},
    )

    from app.models.session import get_active_session

    with app.app_context():
        active = get_active_session(user.id, "https://example.com/training")
    assert active is not None
    assert active.status in ("pending", "running")


def test_get_collection_returns_cached_result(app: Flask, client: FlaskClient) -> None:
    """If a done session already exists, its result is returned immediately."""
    cached = [{"@type": "LearningResource", "name": "Test Material"}]

    with app.app_context():
        user, token = create_user()
        s = create_session(user.id, "https://example.com/training")
        update_session(s.id, "done", result_json=json.dumps(cached))

    response = client.get(
        "/metadata?url=https://example.com/training",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data == cached


def test_get_collection_does_not_enqueue_if_active_session(
    app: Flask, client: FlaskClient
) -> None:
    """If an active (pending/running) session already exists, no new one is created."""
    with app.app_context():
        user, token = create_user()
        create_session(user.id, "https://example.com/training")

    client.get(
        "/metadata?url=https://example.com/training",
        headers={"Authorization": f"Bearer {token}"},
    )

    from app.models.session import get_sessions_for_user

    with app.app_context():
        sessions = get_sessions_for_user(user.id)

    # Only the original pending session should exist
    active_count = sum(1 for s in sessions if s.status in ("pending", "running"))
    assert active_count == 1


def test_get_collection_reenqueues_when_last_session_was_cancelled(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled session must not block re-queueing when cache hash is unchanged."""
    url = "https://example.com/training"
    executed: list[int] = []

    with app.app_context():
        from app.db.sqlite import get_db

        user, token = create_user()
        cancelled = create_session(user.id, url)
        update_session(cancelled.id, "cancelled")
        db = get_db()
        db.execute(
            "INSERT INTO metadata_cache (url, content_hash, structural_summary) VALUES (?, ?, ?)",
            (url, "test-site-hash", '{"schema_version":"2"}'),
        )
        db.commit()

    def _fake_run_extraction(
        session_id: int,
        **_kwargs: object,
    ) -> None:
        executed.append(session_id)

    monkeypatch.setattr("app.api._extraction.run_extraction", _fake_run_extraction)

    response = client.get(
        "/metadata?url=https://example.com/training",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert json.loads(response.data) == []

    with app.app_context():
        from app.api._extraction import run_pending_extractions
        from app.models.session import get_active_session

        active = get_active_session(user.id, url)
        assert active is not None
        assert active.status == "pending"

        executed_ids = run_pending_extractions(app, user_id=user.id, url=url)

    assert executed_ids == [active.id]
    assert executed == [active.id]


def test_get_collection_reenqueues_when_latest_session_cancelled_even_with_older_done(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled latest session must allow re-queue, even with an older done run."""
    url = "https://example.com/training"
    executed: list[int] = []

    with app.app_context():
        from app.db.sqlite import get_db

        user, token = create_user()
        done = create_session(user.id, url)
        update_session(done.id, "done", result_json='[{"name":"cached"}]')
        cancelled = create_session(user.id, url)
        update_session(cancelled.id, "cancelled")
        db = get_db()
        db.execute(
            "INSERT INTO metadata_cache (url, content_hash, structural_summary) VALUES (?, ?, ?)",
            (url, "test-site-hash", '{"schema_version":"2"}'),
        )
        db.commit()

    def _fake_run_extraction(
        app: Flask,
        session_id: int,
        url: str,
        prompt: str | None,
        structural_summary: str | None,
        site_content_hash: str | None = None,
    ) -> None:
        del app, url, prompt, structural_summary, site_content_hash
        executed.append(session_id)

    monkeypatch.setattr("app.api._extraction.run_extraction", _fake_run_extraction)

    response = client.get(
        "/metadata?url=https://example.com/training",
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status_code == 200
    assert json.loads(response.data) == [{"name": "cached"}]

    with app.app_context():
        from app.api._extraction import run_pending_extractions
        from app.models.session import get_active_session

        active = get_active_session(user.id, url)
        assert active is not None
        assert active.status == "pending"

        executed_ids = run_pending_extractions(app, user_id=user.id, url=url)

    assert executed_ids == [active.id]
    assert executed == [active.id]


def test_get_collection_reenqueues_after_cancelling_running_session(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel a running session via endpoint, then /metadata must create a new pending session."""
    url = "https://example.com/training"
    executed: list[int] = []

    with app.app_context():
        from app.db.sqlite import get_db

        user, token = create_user()
        done = create_session(user.id, url)
        update_session(done.id, "done", result_json='[{"name":"cached"}]')
        db = get_db()
        db.execute(
            "INSERT INTO metadata_cache (url, content_hash, structural_summary) VALUES (?, ?, ?)",
            (url, "test-site-hash", '{"schema_version":"2"}'),
        )
        running = create_session(user.id, url)
        update_session(running.id, "running")
        db.commit()

    def _fake_run_extraction(
        app: Flask,
        session_id: int,
        url: str,
        prompt: str | None,
        structural_summary: str | None,
        site_content_hash: str | None = None,
    ) -> None:
        del app, url, prompt, structural_summary, site_content_hash
        executed.append(session_id)

    monkeypatch.setattr("app.api._extraction.run_extraction", _fake_run_extraction)

    client.post("/sessions/login", json={"token": token})
    cancel_response = client.post(f"/sessions/{running.id}/cancel")
    assert cancel_response.status_code in (302, 303)

    response = client.get(
        "/metadata?url=https://example.com/training",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert json.loads(response.data) == [{"name": "cached"}]

    with app.app_context():
        from app.api._extraction import run_pending_extractions
        from app.models.session import get_active_session

        active = get_active_session(user.id, url)
        assert active is not None
        assert active.status == "pending"

        executed_ids = run_pending_extractions(app, user_id=user.id, url=url)

    assert executed_ids == [active.id]
    assert executed == [active.id]


# ---------------------------------------------------------------------------
# GET /metadata/single
# ---------------------------------------------------------------------------


def test_get_single_returns_empty_dict_initially(
    app: Flask, client: FlaskClient
) -> None:
    with app.app_context():
        _user, token = create_user()

    response = client.get(
        "/metadata/single?url=https://example.com/course",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data == {}


def test_get_single_returns_item_when_one_result(
    app: Flask, client: FlaskClient
) -> None:
    single = [{"@type": "LearningResource", "name": "Single Course"}]

    with app.app_context():
        user, token = create_user()
        s = create_session(user.id, "https://example.com/course")
        update_session(s.id, "done", result_json=json.dumps(single))

    response = client.get(
        "/metadata/single?url=https://example.com/course",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data == single[0]


def test_get_single_force_refresh_is_admin_only(app: Flask, client: FlaskClient) -> None:
    with app.app_context():
        _user, token = create_user()

    response = client.get(
        "/metadata/single?url=https://example.com/course&force_refresh=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_get_single_returns_400_for_multiple_results(
    app: Flask, client: FlaskClient
) -> None:
    """If done session has multiple items, /single must return 400."""
    multiple = [
        {"@type": "LearningResource", "name": "Course A"},
        {"@type": "LearningResource", "name": "Course B"},
    ]

    with app.app_context():
        user, token = create_user()
        s = create_session(user.id, "https://example.com/courses")
        update_session(s.id, "done", result_json=json.dumps(multiple))

    response = client.get(
        "/metadata/single?url=https://example.com/courses",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /sessions/login
# ---------------------------------------------------------------------------


def test_sessions_login_with_invalid_token_returns_401(client: FlaskClient) -> None:
    response = client.post(
        "/sessions/login",
        json={"token": "not-a-valid-token"},
    )
    assert response.status_code == 401


def test_sessions_login_with_missing_token_returns_401(client: FlaskClient) -> None:
    response = client.post("/sessions/login", json={})
    assert response.status_code == 401


def test_sessions_login_sets_cookie(app: Flask, client: FlaskClient) -> None:
    with app.app_context():
        _user, token = create_user()

    response = client.post(
        "/sessions/login",
        json={"token": token},
    )
    # Should redirect to /sessions
    assert response.status_code in (302, 303)
    # Verify we can subsequently access /sessions (which requires the session cookie)
    follow_resp = client.get("/sessions")
    assert follow_resp.status_code == 200


def test_sessions_login_via_form_sets_cookie(app: Flask, client: FlaskClient) -> None:
    with app.app_context():
        _user, token = create_user()

    response = client.post(
        "/sessions/login",
        data={"token": token},
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code in (302, 303)


# ---------------------------------------------------------------------------
# GET /sessions
# ---------------------------------------------------------------------------


def test_sessions_view_requires_auth(client: FlaskClient) -> None:
    """Unauthenticated access to /sessions must redirect to login form."""
    response = client.get("/sessions")
    assert response.status_code in (302, 303)


def test_sessions_view_shows_sessions_when_authenticated(
    app: Flask, client: FlaskClient
) -> None:
    with app.app_context():
        user, token = create_user()
        s = create_session(user.id, "https://example.com/training")
        update_session(s.id, "done", result_json=json.dumps([{"name": "Test"}]))

    # Log in first
    client.post("/sessions/login", json={"token": token})

    response = client.get("/sessions")
    assert response.status_code == 200
    assert b"Session Viewer" in response.data
    assert b"example.com" in response.data


def test_sessions_login_form_returns_html(client: FlaskClient) -> None:
    response = client.get("/sessions/login")
    assert response.status_code == 200
    assert b"<form" in response.data


def test_sessions_view_shows_pending_hint_and_cancel_button(
    app: Flask, client: FlaskClient
) -> None:
    with app.app_context():
        user, token = create_user()
        create_session(user.id, "https://example.com/pending")

    client.post("/sessions/login", json={"token": token})
    response = client.get("/sessions")

    assert response.status_code == 200
    assert b"Queued. It will run automatically" in response.data
    assert b"Cancel" in response.data


def test_cancel_pending_session_marks_status_cancelled(
    app: Flask, client: FlaskClient
) -> None:
    with app.app_context():
        user, token = create_user()
        pending_session = create_session(user.id, "https://example.com/pending")

    client.post("/sessions/login", json={"token": token})
    response = client.post(f"/sessions/{pending_session.id}/cancel")

    assert response.status_code in (302, 303)
    with app.app_context():
        cancelled = get_session_by_id(pending_session.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"


def test_cancel_running_session_marks_status_cancelled(
    app: Flask, client: FlaskClient
) -> None:
    with app.app_context():
        user, token = create_user()
        running_session = create_session(user.id, "https://example.com/running")
        update_session(
            running_session.id,
            "running",
            log='[{"id":1,"type":"info","message":"Starting extraction for https://example.com/running"}]',
        )

    client.post("/sessions/login", json={"token": token})
    response = client.post(f"/sessions/{running_session.id}/cancel")

    assert response.status_code in (302, 303)
    with app.app_context():
        cancelled = get_session_by_id(running_session.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
