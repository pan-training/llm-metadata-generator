import atexit
import re
from pathlib import Path

import click
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

# Templates live at the repo root, one level above the app package.
_TEMPLATE_FOLDER = str(Path(__file__).parent.parent / "templates")


# ---------------------------------------------------------------------------
# Integration-test helpers (module-level so they can be unit-tested)
# ---------------------------------------------------------------------------


def _sanitize_url_for_dirname(url: str) -> str:
    """Convert a URL into a short, filesystem-safe directory name component.

    Strips the scheme prefix, replaces all non-alphanumeric characters with
    underscores, collapses runs of underscores, and trims to 80 characters.

    Examples::

        >>> _sanitize_url_for_dirname("https://example.com/training/")
        'example.com_training'
        >>> _sanitize_url_for_dirname("http://training.galaxyproject.org/")
        'training.galaxyproject.org'
    """
    stripped = url.removeprefix("https://").removeprefix("http://")
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", stripped)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe[:80]


def _format_validation_errors(
    items: list[dict[str, object]], schema: "dict[str, object] | None"
) -> list[str]:
    """Return human-readable validation lines for *items* against *schema*.

    Each line describes one item (valid ✓ or lists errors).  Items are
    annotated in-place with a top-level ``_validation`` key so the caller
    can persist the results in ``result.json``.
    """
    if schema is None:
        return []
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(schema)
    lines: list[str] = []
    for i, item in enumerate(items):
        errs = [
            f"{' → '.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
            for e in validator.iter_errors([item])
        ]
        item["_validation"] = {"valid": not errs, "errors": errs}
        label = item.get("name") or item.get("url") or f"item {i}"
        if errs:
            lines.append(f"  item {i} ({label!r}): {len(errs)} error(s)")
            lines.extend(f"    - {err}" for err in errs)
        else:
            lines.append(f"  item {i} ({label!r}): valid ✓")
    return lines


def create_app(config=None) -> Flask:
    """Application factory.

    Args:
        config: Optional mapping of config overrides (useful in tests).

    Returns:
        A configured Flask application instance.
    """
    app = Flask(__name__, template_folder=_TEMPLATE_FOLDER)

    # Load default config from config.py, then apply any overrides.
    app.config.from_object("config")
    if config:
        app.config.update(config)

    # Tear down the per-request DB connection automatically.
    from app.db.sqlite import close_db

    app.teardown_appcontext(close_db)

    # Register CLI command groups.
    _register_db_cli(app)
    _register_users_cli(app)
    _register_integration_test_cli(app)

    # Register blueprints.
    from app.api.health import bp as health_bp

    app.register_blueprint(health_bp)

    from app.api.collection import bp as collection_bp
    from app.api.resource import bp as resource_bp
    from app.api.sessions import bp as sessions_bp

    app.register_blueprint(collection_bp)
    app.register_blueprint(resource_bp)
    app.register_blueprint(sessions_bp)

    # Start the background scheduler unless we are in testing mode.
    if not app.config.get("TESTING"):
        scheduler = BackgroundScheduler()
        scheduler.start()
        app.extensions["scheduler"] = scheduler
        atexit.register(lambda: scheduler.shutdown(wait=False))

    return app


def _register_db_cli(app: Flask) -> None:
    """Register the ``flask db`` command group."""

    @app.cli.group()
    def db():
        """Database management commands."""

    @db.command("init")
    def init_db_command():
        """Initialise the database (create tables from schema.sql)."""
        from app.db.sqlite import init_db

        init_db()
        click.echo("Database initialised.")


def _register_users_cli(app: Flask) -> None:
    """Register the ``flask users`` command group."""

    @app.cli.group()
    def users():
        """User management commands."""

    @users.command("create")
    @click.option("--admin", is_flag=True, default=False, help="Grant admin privileges.")
    def create_user_command(admin: bool) -> None:
        """Create a new user and print their Bearer token."""
        from app.models.user import create_user

        user, token = create_user(is_admin=admin)
        role = "admin" if user.is_admin else "user"
        click.echo(f"Created {role} (id={user.id}).")
        click.echo(f"Token: {token}")

    @users.command("list")
    def list_users_command() -> None:
        """List all users."""
        from app.models.user import list_users

        all_users = list_users()
        if not all_users:
            click.echo("No users found.")
            return
        for user in all_users:
            role = "admin" if user.is_admin else "user"
            click.echo(f"id={user.id}  created={user.created_at}  role={role}")

    @users.command("revoke")
    @click.argument("identifier")
    def revoke_user_command(identifier: str) -> None:
        """Revoke a user account.

        IDENTIFIER may be the numeric user id (shown by ``flask users
        list``), the plaintext Bearer token, or its SHA-256 hex digest
        (64 lowercase hex characters).
        """
        from app.models.user import revoke_user

        if revoke_user(identifier):
            click.echo("Token revoked.")
        else:
            click.echo("Token not found.", err=True)


def _register_integration_test_cli(app: Flask) -> None:
    """Register the ``flask integration-test`` command group."""

    @app.cli.group("integration-test")
    def integration_test() -> None:
        """Integration test commands: run extraction against real websites."""

    @integration_test.command("run")
    @click.option("--url", default=None, help="Only run this URL (bypasses config if not listed).")
    @click.option(
        "--config",
        "config_path",
        default=None,
        help="Path to sites config JSON (default: integration_test/config.json).",
    )
    @click.option(
        "--output-dir",
        default=None,
        help="Results directory (default: integration_test/results).",
    )
    @click.option("--prompt", default=None, help="Override extraction prompt for all sites.")
    def run_command(
        url: str | None,
        config_path: str | None,
        output_dir: str | None,
        prompt: str | None,
    ) -> None:
        """Run extraction against real websites and save detailed results.

        Results are saved to integration_test/results/<site>__<timestamp>/
        and can be committed to share with collaborators.
        """
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        from app.agents import get_llm_client
        from app.agents.bioschemas import (
            AccessDeniedError,
            BioschemasExtractorAgent,
            NotTrainingContentError,
            compute_structural_summary,
        )

        root = Path(__file__).parent.parent  # repo root
        cfg_file = (
            Path(config_path)
            if config_path
            else root / "integration_test" / "config.json"
        )
        results_base = (
            Path(output_dir)
            if output_dir
            else root / "integration_test" / "results"
        )
        results_base.mkdir(parents=True, exist_ok=True)

        if not cfg_file.exists():
            click.echo(f"Config file not found: {cfg_file}", err=True)
            raise SystemExit(1)

        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        sites: list[dict[str, str | None]] = cfg.get("sites", [])

        if url:
            # Allow running an ad-hoc URL not in the config file.
            matched = [s for s in sites if s.get("url") == url]
            sites = matched if matched else [{"url": url, "description": "", "prompt": None}]

        if not sites:
            click.echo("No sites to test.", err=True)
            raise SystemExit(1)

        # Load schema once for all validation passes.
        schema_path = root / "docs" / "Bioschemas" / "bioschemas-training-schema.json"
        schema: dict[str, object] | None = None
        if schema_path.exists():
            schema = json.loads(schema_path.read_text(encoding="utf-8"))

        client = get_llm_client()
        agent = BioschemasExtractorAgent()

        click.echo(f"Running integration tests for {len(sites)} site(s) …")

        for site_cfg in sites:
            site_url = str(site_cfg.get("url") or "")
            if not site_url:
                continue
            site_prompt = prompt or site_cfg.get("prompt")  # type: ignore[assignment]
            description = site_cfg.get("description", "")

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
            run_dir = (
                results_base / f"{_sanitize_url_for_dirname(site_url)}__{timestamp}"
            )
            run_dir.mkdir(parents=True)

            click.echo(f"\n{'=' * 60}")
            click.echo(f"Site       : {site_url}")
            if description:
                click.echo(f"Description: {description}")
            click.echo(f"Output dir : {run_dir}")

            # Persist the inputs used for this run.
            (run_dir / "config.json").write_text(
                json.dumps(
                    {
                        "url": site_url,
                        "description": description,
                        "prompt": site_prompt,
                        "timestamp": timestamp,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            log_entries: list[str] = []

            def _log(msg: str) -> None:
                log_entries.append(msg)
                click.echo(f"  {msg}")

            items: list[dict[str, object]] = []
            error: str | None = None
            try:
                items = agent.run(
                    url=site_url,
                    prompt=site_prompt,  # type: ignore[arg-type]
                    structural_summary=None,
                    llm_client=client,
                    log_fn=_log,
                )
            except AccessDeniedError as exc:
                error = f"AccessDeniedError: {exc}"
                click.echo(f"  ERROR: {error}", err=True)
            except NotTrainingContentError as exc:
                error = f"NotTrainingContentError: {exc}"
                click.echo(f"  ERROR: {error}", err=True)
            except Exception as exc:  # Note: Exception intentionally excludes BaseException
                error = f"{type(exc).__name__}: {exc}"
                click.echo(f"  UNEXPECTED ERROR: {error}", err=True)

            # Save agent log.
            (run_dir / "log.txt").write_text(
                "\n".join(log_entries),
                encoding="utf-8",
            )

            # Validate items (annotates in-place with _validation key) and save.
            validation_lines = _format_validation_errors(items, schema)
            (run_dir / "result.json").write_text(
                json.dumps(items, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            # Save structural summary for future incremental runs.
            if items:
                (run_dir / "structural_summary.json").write_text(
                    compute_structural_summary(items, site_url),
                    encoding="utf-8",
                )

            # Write human-readable summary.
            summary_lines = [
                "# Integration test summary",
                "",
                f"URL        : {site_url}",
                f"Timestamp  : {timestamp}",
                f"Items      : {len(items)}",
                f"Error      : {error or 'none'}",
                "",
                "## Validation",
                *(validation_lines or ["  (no items to validate)"]),
                "",
                "## Agent log",
                *(f"  {e}" for e in log_entries),
            ]
            (run_dir / "summary.md").write_text(
                "\n".join(summary_lines),
                encoding="utf-8",
            )

            status = "OK" if error is None else "ERROR"
            click.echo(f"\n  => {status}: {len(items)} item(s) extracted")
            click.echo(f"  => Results saved to {run_dir.relative_to(root)}")

        click.echo("\nIntegration tests complete.")

