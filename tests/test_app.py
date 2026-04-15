"""Smoke tests for the Flask application factory and database setup."""

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import create_app


@pytest.fixture
def app():
    """Create a test application instance with an in-memory database."""
    return create_app({"TESTING": True, "DATABASE_URL": ":memory:"})


@pytest.fixture(autouse=True)
def disable_site_hash_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent app tests from performing live network fetches for site hashes."""
    monkeypatch.setattr(
        "app.api._extraction._fetch_site_content_hash",
        lambda _url: "test-site-hash",
    )


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


def test_tasks_trigger_metadata_command_invokes_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": str(tmp_path / "test.db")})

    with app.app_context():
        from app.db.sqlite import init_db
        from app.models.user import create_user

        init_db()
        user, _token = create_user()

    captured: dict[str, Any] = {}

    def _fake_trigger_extraction_now(
        app: Any, user_id: int, url: str, prompt: str | None
    ) -> int:
        captured["app"] = app
        captured["user_id"] = user_id
        captured["url"] = url
        captured["prompt"] = prompt
        return 123

    monkeypatch.setattr(
        "app.api._extraction.trigger_extraction_now",
        _fake_trigger_extraction_now,
    )

    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "tasks",
            "trigger-metadata",
            "--user-id",
            str(user.id),
            "--url",
            "https://example.com/training",
            "--prompt",
            "focus on workshop data",
        ]
    )

    assert result.exit_code == 0
    assert "Triggered metadata extraction (session_id=123)." in result.output
    assert captured["user_id"] == user.id
    assert captured["url"] == "https://example.com/training"
    assert captured["prompt"] == "focus on workshop data"


def test_tasks_trigger_metadata_command_fails_for_unknown_user(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": str(tmp_path / "test.db")})

    with app.app_context():
        from app.db.sqlite import init_db

        init_db()

    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "tasks",
            "trigger-metadata",
            "--user-id",
            "9999",
            "--url",
            "https://example.com/training",
        ]
    )

    assert result.exit_code != 0
    assert "User id 9999 not found." in result.output


def test_tasks_run_queued_command_executes_pending_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": str(tmp_path / "test.db")})

    with app.app_context():
        from app.db.sqlite import init_db

        init_db()

    captured: dict[str, Any] = {}

    def _fake_run_pending_extractions(
        app: Any, user_id: int | None = None, url: str | None = None
    ) -> list[int]:
        captured["user_id"] = user_id
        captured["url"] = url
        return [5, 6]

    monkeypatch.setattr(
        "app.api._extraction.run_pending_extractions",
        _fake_run_pending_extractions,
    )

    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "tasks",
            "run-queued",
            "--user-id",
            "7",
            "--url",
            "https://example.com/training",
        ]
    )

    assert result.exit_code == 0
    assert "Executed 2 queued metadata task(s): 5, 6" in result.output
    assert captured["user_id"] == 7
    assert captured["url"] == "https://example.com/training"


def test_tasks_run_queued_command_when_none_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": str(tmp_path / "test.db")})

    with app.app_context():
        from app.db.sqlite import init_db

        init_db()

    def _fake_run_pending_extractions(
        app: Any, user_id: int | None = None, url: str | None = None
    ) -> list[int]:
        return []

    monkeypatch.setattr(
        "app.api._extraction.run_pending_extractions",
        _fake_run_pending_extractions,
    )

    runner = app.test_cli_runner()
    result = runner.invoke(args=["tasks", "run-queued"])

    assert result.exit_code == 0
    assert "No queued metadata tasks found." in result.output


def test_run_pending_extractions_continues_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": str(tmp_path / "test.db")})

    with app.app_context():
        from app.api._extraction import run_pending_extractions
        from app.db.sqlite import init_db
        from app.models.user import create_user
        from app.models.session import create_session

        init_db()
        user, _token = create_user()
        first = create_session(user.id, "https://example.com/one")
        second = create_session(user.id, "https://example.com/two")

        call_order: list[int] = []

        def _fake_run_extraction(
            app: Any,
            session_id: int,
            url: str,
            prompt: str | None,
            structural_summary: str | None,
            site_content_hash: str | None = None,
        ) -> None:
            _ = (app, url, prompt, structural_summary, site_content_hash)
            call_order.append(session_id)
            if session_id == first.id:
                raise RuntimeError("boom")

        monkeypatch.setattr("app.api._extraction.run_extraction", _fake_run_extraction)
        monkeypatch.setattr(
            "app.api._extraction._fetch_site_content_hash",
            lambda _url: "test-site-hash",
        )

        executed_ids = run_pending_extractions(app)

    assert call_order == [first.id, second.id]
    assert executed_ids == [second.id]


def test_run_pending_extractions_includes_stale_running_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": str(tmp_path / "test.db")})

    with app.app_context():
        from app.api._extraction import run_pending_extractions
        from app.db.sqlite import init_db
        from app.models.session import create_session, update_session
        from app.models.user import create_user

        init_db()
        user, _token = create_user()
        stale_running = create_session(user.id, "https://example.com/stale")
        update_session(stale_running.id, "running", log="[]")

        called: list[int] = []

        def _fake_run_extraction(
            app: Any,
            session_id: int,
            url: str,
            prompt: str | None,
            structural_summary: str | None,
            site_content_hash: str | None = None,
        ) -> None:
            _ = (app, url, prompt, structural_summary, site_content_hash)
            called.append(session_id)

        monkeypatch.setattr("app.api._extraction.run_extraction", _fake_run_extraction)

        executed_ids = run_pending_extractions(app)

    assert called == [stale_running.id]
    assert executed_ids == [stale_running.id]


def test_run_pending_extractions_includes_startup_only_running_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": str(tmp_path / "test.db")})

    with app.app_context():
        from app.api._extraction import run_pending_extractions
        from app.db.sqlite import init_db
        from app.models.session import create_session, update_session
        from app.models.user import create_user

        init_db()
        user, _token = create_user()
        stale_running = create_session(user.id, "https://example.com/stale-startup")
        update_session(
            stale_running.id,
            "running",
            log='[{"id":1,"type":"info","message":"Starting extraction for https://example.com/stale-startup"}]',
        )

        called: list[int] = []

        def _fake_run_extraction(
            app: Any,
            session_id: int,
            url: str,
            prompt: str | None,
            structural_summary: str | None,
            site_content_hash: str | None = None,
        ) -> None:
            _ = (app, url, prompt, structural_summary, site_content_hash)
            called.append(session_id)

        monkeypatch.setattr("app.api._extraction.run_extraction", _fake_run_extraction)

        executed_ids = run_pending_extractions(app)

    assert called == [stale_running.id]
    assert executed_ids == [stale_running.id]


def test_enqueue_extraction_skips_when_site_hash_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": str(tmp_path / "test.db")})

    class _FakeScheduler:
        def __init__(self) -> None:
            self.jobs: list[dict[str, Any]] = []

        def add_job(
            self,
            *,
            func: Any,
            trigger: str,
            kwargs: dict[str, Any],
            id: str | None = None,
            replace_existing: bool = False,
        ) -> None:
            _ = (func, trigger, id, replace_existing)
            self.jobs.append(kwargs)

    fake_scheduler = _FakeScheduler()

    with app.app_context():
        from app.api._extraction import enqueue_extraction_if_needed
        from app.db.sqlite import get_db, init_db
        from app.models.user import create_user

        init_db()
        user, _token = create_user()
        app.extensions["scheduler"] = fake_scheduler
        db = get_db()
        db.execute(
            "INSERT INTO metadata_cache (url, content_hash, structural_summary) VALUES (?, ?, ?)",
            ("https://example.com/training", "same-hash", '{"schema_version":"2"}'),
        )
        db.commit()

        monkeypatch.setattr(
            "app.api._extraction._fetch_site_content_hash",
            lambda _url: "same-hash",
        )

        enqueue_extraction_if_needed("https://example.com/training", None, user.id)

        session_count = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    assert session_count == 0
    assert fake_scheduler.jobs == []


def test_enqueue_extraction_uses_incremental_mode_on_hash_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": str(tmp_path / "test.db"),
            "CRON_METADATA_FULL_REFRESH_PROBABILITY": 0.0,
        }
    )

    class _FakeScheduler:
        def __init__(self) -> None:
            self.jobs: list[dict[str, Any]] = []

        def add_job(
            self,
            *,
            func: Any,
            trigger: str,
            kwargs: dict[str, Any],
            id: str | None = None,
            replace_existing: bool = False,
        ) -> None:
            _ = (func, trigger, id, replace_existing)
            self.jobs.append(kwargs)

    fake_scheduler = _FakeScheduler()

    with app.app_context():
        from app.api._extraction import enqueue_extraction_if_needed
        from app.db.sqlite import get_db, init_db
        from app.models.user import create_user

        init_db()
        user, _token = create_user()
        app.extensions["scheduler"] = fake_scheduler
        db = get_db()
        db.execute(
            "INSERT INTO metadata_cache (url, content_hash, structural_summary) VALUES (?, ?, ?)",
            ("https://example.com/training", "old-hash", '{"schema_version":"2","hint":"keep"}'),
        )
        db.commit()

        monkeypatch.setattr(
            "app.api._extraction._fetch_site_content_hash",
            lambda _url: "new-hash",
        )

        enqueue_extraction_if_needed("https://example.com/training", None, user.id)

    assert len(fake_scheduler.jobs) == 1
    assert fake_scheduler.jobs[0]["structural_summary"] == '{"schema_version":"2","hint":"keep"}'
    assert fake_scheduler.jobs[0]["site_content_hash"] == "new-hash"


def test_enqueue_extraction_random_full_refresh_is_configurable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": str(tmp_path / "test.db"),
            "CRON_METADATA_FULL_REFRESH_PROBABILITY": 1.0,
        }
    )

    class _FakeScheduler:
        def __init__(self) -> None:
            self.jobs: list[dict[str, Any]] = []

        def add_job(
            self,
            *,
            func: Any,
            trigger: str,
            kwargs: dict[str, Any],
            id: str | None = None,
            replace_existing: bool = False,
        ) -> None:
            _ = (func, trigger, id, replace_existing)
            self.jobs.append(kwargs)

    fake_scheduler = _FakeScheduler()

    with app.app_context():
        from app.api._extraction import enqueue_extraction_if_needed
        from app.db.sqlite import get_db, init_db
        from app.models.user import create_user

        init_db()
        user, _token = create_user()
        app.extensions["scheduler"] = fake_scheduler
        db = get_db()
        db.execute(
            "INSERT INTO metadata_cache (url, content_hash, structural_summary) VALUES (?, ?, ?)",
            ("https://example.com/training", "same-hash", '{"schema_version":"2","hint":"drop"}'),
        )
        db.commit()

        monkeypatch.setattr(
            "app.api._extraction._fetch_site_content_hash",
            lambda _url: "same-hash",
        )
        monkeypatch.setattr("app.api._extraction.random.random", lambda: 0.0)

        enqueue_extraction_if_needed("https://example.com/training", None, user.id)

    assert len(fake_scheduler.jobs) == 1
    assert fake_scheduler.jobs[0]["structural_summary"] is None


def test_enqueue_extraction_detects_changes_on_cached_subpages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": str(tmp_path / "test.db"),
            "CRON_METADATA_FULL_REFRESH_PROBABILITY": 0.0,
        }
    )

    class _FakeScheduler:
        def __init__(self) -> None:
            self.jobs: list[dict[str, Any]] = []

        def add_job(
            self,
            *,
            func: Any,
            trigger: str,
            kwargs: dict[str, Any],
            id: str | None = None,
            replace_existing: bool = False,
        ) -> None:
            _ = (func, trigger, id, replace_existing)
            self.jobs.append(kwargs)

    fake_scheduler = _FakeScheduler()

    with app.app_context():
        from app.api._extraction import _snapshot_content_hash, enqueue_extraction_if_needed
        from app.db.sqlite import get_db, init_db
        from app.models.user import create_user

        init_db()
        user, _token = create_user()
        app.extensions["scheduler"] = fake_scheduler
        db = get_db()
        db.execute(
            "INSERT INTO metadata_cache (url, content_hash, structural_summary) VALUES (?, ?, ?)",
            (
                "https://example.com/training",
                "root-hash",
                (
                    '{"schema_version":"2","crawled_page_hashes":{'
                    '"https://example.com/training":"root-hash",'
                    '"https://example.com/training/page-2":"old-page-hash"}}'
                ),
            ),
        )
        db.commit()

        hash_by_url = {
            "https://example.com/training": "root-hash",
            "https://example.com/training/page-2": "new-page-hash",
        }
        monkeypatch.setattr(
            "app.api._extraction._fetch_site_content_hash",
            lambda current_url: hash_by_url.get(current_url),
        )

        enqueue_extraction_if_needed("https://example.com/training", None, user.id)

        expected_snapshot_hash = _snapshot_content_hash(
            {
                "https://example.com/training": "root-hash",
                "https://example.com/training/page-2": "new-page-hash",
            },
            "root-hash",
        )

    assert len(fake_scheduler.jobs) == 1
    assert fake_scheduler.jobs[0]["site_content_hash"] == expected_snapshot_hash


def test_enqueue_extraction_skips_when_cached_subpages_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": str(tmp_path / "test.db"),
            "CRON_METADATA_FULL_REFRESH_PROBABILITY": 0.0,
        }
    )

    class _FakeScheduler:
        def __init__(self) -> None:
            self.jobs: list[dict[str, Any]] = []

        def add_job(
            self,
            *,
            func: Any,
            trigger: str,
            kwargs: dict[str, Any],
            id: str | None = None,
            replace_existing: bool = False,
        ) -> None:
            _ = (func, trigger, id, replace_existing)
            self.jobs.append(kwargs)

    fake_scheduler = _FakeScheduler()

    with app.app_context():
        from app.api._extraction import _snapshot_content_hash, enqueue_extraction_if_needed
        from app.db.sqlite import get_db, init_db
        from app.models.user import create_user

        init_db()
        user, _token = create_user()
        app.extensions["scheduler"] = fake_scheduler
        cached_page_hashes = {
            "https://example.com/training": "root-hash",
            "https://example.com/training/page-2": "page-hash",
        }
        db = get_db()
        db.execute(
            "INSERT INTO metadata_cache (url, content_hash, structural_summary) VALUES (?, ?, ?)",
            (
                "https://example.com/training",
                _snapshot_content_hash(cached_page_hashes, "root-hash"),
                (
                    '{"schema_version":"2","crawled_page_hashes":{'
                    '"https://example.com/training":"root-hash",'
                    '"https://example.com/training/page-2":"page-hash"}}'
                ),
            ),
        )
        db.commit()

        monkeypatch.setattr(
            "app.api._extraction._fetch_site_content_hash",
            lambda current_url: cached_page_hashes.get(current_url),
        )

        enqueue_extraction_if_needed("https://example.com/training", None, user.id)

        session_count = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    assert session_count == 0
    assert fake_scheduler.jobs == []
