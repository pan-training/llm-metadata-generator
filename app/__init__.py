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
    @click.option(
        "--timeout",
        default=None,
        type=int,
        help=(
            "Per-site timeout in seconds.  When the extraction for a single site"
            " exceeds this limit the run is aborted for that site (partial results"
            " are saved) and the next site is started immediately."
        ),
    )
    def run_command(
        url: str | None,
        config_path: str | None,
        output_dir: str | None,
        prompt: str | None,
        timeout: int | None,
    ) -> None:
        """Run extraction against real websites and save detailed results.

        Results are saved to integration_test/results/<site>__<timestamp>/
        and can be committed to share with collaborators.
        """
        import concurrent.futures
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        from app.agents import get_llm_client
        from app.agents.bioschemas import (
            AccessDeniedError,
            BioschemasExtractorAgent,
            NotTrainingContentError,
            compute_site_structure_summary,
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

            log_file = run_dir / "log.txt"
            log_fh = log_file.open("w", encoding="utf-8")

            from app.agents.logger import (
                AgentEvent,
                AgentLogger,
                FetchEvent,
                InfoEvent,
                ItemFoundEvent,
                LLMCallEvent,
                ValidationEvent,
                WarnEvent,
            )

            # Track parent depths so child events are indented on the console.
            _depth_cache: dict[int, int] = {}

            def _event_depth(ev: AgentEvent) -> int:
                if ev.parent_id is None:
                    depth = 0
                else:
                    depth = _depth_cache.get(ev.parent_id, 0) + 1
                _depth_cache[ev.id] = depth
                return depth

            def _format_console_line(ev: AgentEvent, depth: int) -> str | None:
                """Return a human-readable console line for *ev*, or None to skip."""
                indent = "  " * depth
                if isinstance(ev, InfoEvent):
                    return f"{indent}{ev.message}"
                if isinstance(ev, WarnEvent):
                    return f"{indent}⚠  {ev.message}"
                if isinstance(ev, LLMCallEvent):
                    return (
                        f"{indent}[LLM:{ev.task}] {ev.latency_ms:.0f} ms"
                    )
                if isinstance(ev, FetchEvent):
                    return f"{indent}↓ {ev.url} [{ev.status_code}]"
                if isinstance(ev, ItemFoundEvent):
                    return f"{indent}★ {ev.title!r} ({ev.item_type})"
                if isinstance(ev, ValidationEvent):
                    status = "✓" if ev.passed else "✗"
                    errs = f" – {len(ev.errors)} error(s)" if ev.errors else ""
                    return f"{indent}{status} {ev.item_name}{errs}"
                return None

            # Create logger first so _on_event can reference it without a
            # forward declaration; the callback is assigned immediately after.
            run_logger = AgentLogger()
            _log_write_count = 0

            def _on_event(ev: AgentEvent) -> None:
                """Stream each event to console + log.txt immediately."""
                nonlocal _log_write_count
                depth = _event_depth(ev)
                line = _format_console_line(ev, depth)
                if line:
                    click.echo(f"  {line}")
                    log_fh.write(line + "\n")
                    log_fh.flush()
                # Write log.json every 10 events to balance real-time visibility
                # against the O(n²) cost of rewriting the whole file on every event.
                _log_write_count += 1
                if _log_write_count % 10 == 0:
                    (run_dir / "log.json").write_text(
                        run_logger.to_json(), encoding="utf-8"
                    )

            run_logger.on_event = _on_event

            items: list[dict[str, object]] = []

            def _on_item(item: dict[str, object]) -> None:
                """Write partial result.json after each item is extracted."""
                items.append(item)
                (run_dir / "result.json").write_text(
                    json.dumps(items, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

            error: str | None = None
            structural_summary: str | None = None

            def _run_extraction() -> str | None:
                """Run Phase 0 + Phase 1/2 for the current site.

                Returns an error string on failure, or ``None`` on success.
                Mutations to *structural_summary* are visible to the outer
                scope via the nonlocal declaration.
                """
                nonlocal structural_summary

                # Phase 0: compute structural summary before extraction.
                try:
                    run_logger.info("Computing structural summary (Phase 0) …")
                    structural_summary = compute_site_structure_summary(
                        url=site_url,
                        llm_client=client,
                        logger=run_logger,
                    )
                    (run_dir / "structural_summary.json").write_text(
                        structural_summary,
                        encoding="utf-8",
                    )
                    run_logger.info("Structural summary saved to structural_summary.json")
                except Exception as exc:
                    _phase0_err = f"Structural summary failed: {exc}"
                    run_logger.warn(f"{_phase0_err}; proceeding without structural summary")
                    # Don't abort – extraction can still run without a summary.

                # Phase 1/2: crawl + extract.
                try:
                    # items is populated incrementally via on_item so that
                    # partial results are written to result.json even if the
                    # agent raises an exception mid-run.
                    agent.run(
                        url=site_url,
                        prompt=site_prompt,  # type: ignore[arg-type]
                        structural_summary=structural_summary,
                        llm_client=client,
                        logger=run_logger,
                        on_item=_on_item,
                    )
                except AccessDeniedError as exc:
                    err = f"AccessDeniedError: {exc}"
                    click.echo(f"  ERROR: {err}", err=True)
                    return err
                except NotTrainingContentError as exc:
                    err = f"NotTrainingContentError: {exc}"
                    click.echo(f"  ERROR: {err}", err=True)
                    return err
                except Exception as exc:  # Note: Exception intentionally excludes BaseException
                    err = f"{type(exc).__name__}: {exc}"
                    click.echo(f"  UNEXPECTED ERROR: {err}", err=True)
                    return err
                return None

            try:
                if timeout is not None:
                    click.echo(f"  Timeout   : {timeout}s per site")
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_run_extraction)
                        try:
                            error = future.result(timeout=timeout)
                        except concurrent.futures.TimeoutError:
                            error = f"Timeout: site exceeded {timeout}s limit"
                            click.echo(f"  TIMEOUT: {error}", err=True)
                else:
                    error = _run_extraction()
            finally:
                log_fh.close()
                # Write the final log.json (events were already written
                # incrementally, but this ensures a complete flush at the end).
                (run_dir / "log.json").write_text(
                    run_logger.to_json(), encoding="utf-8"
                )
                # Print per-phase timing summary to console
                summary = run_logger.summary()
                click.echo(
                    f"  LLM: {summary['llm_calls']} call(s), "
                    f"{summary['total_llm_ms']:.0f} ms total"
                )
                by_task = summary.get("llm_by_task", {})
                for task_name, stats in by_task.items():
                    click.echo(
                        f"    {task_name}: {stats['count']} call(s), "
                        f"{stats['total_ms']:.0f} ms"
                    )

            # Validate items (annotates in-place with _validation key) and
            # overwrite result.json with the annotated version.
            validation_lines = _format_validation_errors(items, schema)
            (run_dir / "result.json").write_text(
                json.dumps(items, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            # Write human-readable summary (includes per-phase LLM timing).
            # structural_summary.json was already saved in Phase 0 above.
            run_summary = run_logger.summary()
            timing_lines: list[str] = [
                "",
                "## LLM timing",
                f"  Total: {run_summary['llm_calls']} call(s), "
                f"{run_summary['total_llm_ms']:.0f} ms",
            ]
            for task_name, stats in run_summary.get("llm_by_task", {}).items():
                timing_lines.append(
                    f"  {task_name}: {stats['count']} call(s), {stats['total_ms']:.0f} ms"
                )
            log_entries = [
                ev.message
                for ev in run_logger.events
                if isinstance(ev, (InfoEvent, WarnEvent))
            ]
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
                *timing_lines,
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

