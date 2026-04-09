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
    _register_users_cli(app)

    # Register blueprints.
    from app.api.health import bp as health_bp

    app.register_blueprint(health_bp)

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

        user = create_user(is_admin=admin)
        role = "admin" if user.is_admin else "user"
        click.echo(f"Created {role} (id={user.id}).")
        click.echo(f"Token: {user.token}")

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
    @click.argument("token")
    def revoke_user_command(token: str) -> None:
        """Revoke a user token (delete the user row)."""
        from app.db.sqlite import get_db

        db = get_db()
        result = db.execute("DELETE FROM users WHERE token = ?", (token,))
        db.commit()
        if result.rowcount:
            click.echo("Token revoked.")
        else:
            click.echo("Token not found.", err=True)

