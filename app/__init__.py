import atexit

import click
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask


def create_app(config=None) -> Flask:
    """Application factory.

    Args:
        config: Optional mapping of config overrides (useful in tests).

    Returns:
        A configured Flask application instance.
    """
    app = Flask(__name__)

    # Load default config from config.py, then apply any overrides.
    app.config.from_object("config")
    if config:
        app.config.update(config)

    # Tear down the per-request DB connection automatically.
    from app.db.sqlite import close_db

    app.teardown_appcontext(close_db)

    # Register CLI command groups.
    _register_db_cli(app)

    # Start the background scheduler unless we are in testing mode.
    if not app.config.get("TESTING"):
        scheduler = BackgroundScheduler()
        scheduler.start()
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
