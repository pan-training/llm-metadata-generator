"""Session viewer routes.

GET /                 – navigation index
POST /sessions/login  – accepts JSON {token: ...} or form data, sets signed cookie
GET /sessions         – session viewer protected by signed cookie
GET /sessions/login   – show the login form
GET /integration-tests – admin-only view of integration test runs
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.jobstores.base import JobLookupError
from flask import (
    Blueprint,
    Response,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue

from app.db.sqlite import get_db
from app.models.user import get_user_by_token

bp = Blueprint("sessions_viewer", __name__)

# Paths to log-export directories relative to the repo root.
_REPO_ROOT = Path(__file__).parent.parent.parent
_INTEGRATION_RESULTS_DIR = _REPO_ROOT / "integration_test" / "results"
_ARCHIVED_RUN_RESULTS_DIR = _REPO_ROOT / "archived_run" / "results"


@bp.get("/")
def index() -> ResponseReturnValue:
    """Simple navigation index listing all endpoints."""
    return render_template("index.html")


@bp.get("/sessions/login")
def login_form() -> ResponseReturnValue:
    """Show the login form."""
    return Response(
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Session Viewer Login</title>
  <style>
    body { font-family: sans-serif; max-width: 400px; margin: 80px auto; padding: 0 16px; }
    input { width: 100%; padding: 8px; margin: 8px 0; box-sizing: border-box; }
    button { padding: 8px 16px; background: #1a73e8; color: white; border: none; cursor: pointer; }
    button:hover { background: #1557b0; }
    .error { color: red; margin-top: 8px; }
  </style>
</head>
<body>
  <h2>Session Viewer Login</h2>
  <form method="POST" action="/sessions/login">
    <label>Bearer Token</label>
    <input type="password" name="token" placeholder="Paste your token here" required>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>""",
        mimetype="text/html",
    )


@bp.post("/sessions/login")
def login() -> ResponseReturnValue:
    """Accept token via JSON body or form data, set session cookie."""
    token: str | None = None

    if request.is_json:
        data = request.get_json(silent=True) or {}
        token = data.get("token")
    else:
        token = request.form.get("token")

    if not token:
        return Response("Missing token", status=401)

    user = get_user_by_token(token)
    if user is None:
        return Response("Invalid token", status=401)

    session["user_id"] = user.id
    session["is_admin"] = user.is_admin
    return redirect(url_for("sessions_viewer.sessions_view"))


@bp.get("/sessions")
def sessions_view() -> ResponseReturnValue:
    """Show sessions for the authenticated user."""
    user_id: int | None = session.get("user_id")
    if not user_id:
        return redirect(url_for("sessions_viewer.login_form"))

    from app.models.session import get_sessions_for_user

    user_sessions = get_sessions_for_user(user_id)
    is_admin: bool = bool(session.get("is_admin", False))
    pending_next_run_by_session: dict[int, str] = {}
    scheduler = current_app.extensions.get("scheduler")
    if scheduler is not None:
        for job in scheduler.get_jobs():
            if not job.id.startswith("session-extraction-"):
                continue
            session_id = job.kwargs.get("session_id")
            next_run_time = job.next_run_time
            if isinstance(session_id, int) and next_run_time is not None:
                pending_next_run_by_session[session_id] = next_run_time.astimezone(
                    timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S UTC")

    return render_template(
        "sessions.html",
        sessions=user_sessions,
        user_id=user_id,
        is_admin=is_admin,
        pending_next_run_by_session=pending_next_run_by_session,
    )


@bp.post("/sessions/<int:session_id>/cancel")
def cancel_session_view(session_id: int) -> ResponseReturnValue:
    """Cancel a pending or running session for the authenticated user."""
    user_id: int | None = session.get("user_id")
    if not user_id:
        return redirect(url_for("sessions_viewer.login_form"))

    from app.api._extraction import build_extraction_job_id
    from app.models.session import cancel_session

    cancelled = cancel_session(session_id=session_id, user_id=user_id)
    if cancelled:
        scheduler = current_app.extensions.get("scheduler")
        if scheduler is not None:
            try:
                scheduler.remove_job(build_extraction_job_id(session_id))
            except JobLookupError:
                pass

    return redirect(url_for("sessions_viewer.sessions_view"))


@bp.get("/integration-tests")
def integration_tests_view() -> ResponseReturnValue:
    """Admin-only view of integration test runs from integration_test/results/."""
    user_id: int | None = session.get("user_id")
    if not user_id:
        return redirect(url_for("sessions_viewer.login_form"))

    if not session.get("is_admin"):
        return Response("Forbidden – admin access required", status=403)

    runs: list[dict] = []
    if _INTEGRATION_RESULTS_DIR.is_dir():
        for run_dir in sorted(_INTEGRATION_RESULTS_DIR.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            cfg_file = run_dir / "config.json"
            if not cfg_file.exists():
                continue
            try:
                cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cfg = {}

            # Prefer structured log.json; fall back to plain log.txt
            log_json: str | None = None
            log_json_path = run_dir / "log.json"
            log_txt_path = run_dir / "log.txt"
            if log_json_path.exists():
                log_json = log_json_path.read_text(encoding="utf-8")
            elif log_txt_path.exists():
                log_json = log_txt_path.read_text(encoding="utf-8")

            result_json: str | None = None
            result_path = run_dir / "result.json"
            if result_path.exists():
                result_json = result_path.read_text(encoding="utf-8")

            summary_md: str | None = None
            summary_path = run_dir / "summary.md"
            if summary_path.exists():
                summary_md = summary_path.read_text(encoding="utf-8")

            # Count items and check for errors in summary.md
            item_count: int | None = None
            has_error = False
            if summary_md:
                for line in summary_md.splitlines():
                    if line.startswith("Items      :"):
                        try:
                            item_count = int(line.split(":")[1].strip())
                        except (ValueError, IndexError):
                            pass
                    if line.startswith("Error      :") and "none" not in line:
                        has_error = True

            runs.append(
                {
                    "run_id": run_dir.name,
                    "url": cfg.get("url", ""),
                    "description": cfg.get("description", ""),
                    "timestamp": cfg.get("timestamp", run_dir.name),
                    "log": log_json,
                    "result_json": result_json,
                    "summary_md": summary_md,
                    "item_count": item_count,
                    "has_error": has_error,
                }
            )

    return render_template(
        "integration_tests.html",
        runs=runs,
        user_id=user_id,
    )


@bp.get("/archived-runs")
def archived_runs_view() -> ResponseReturnValue:
    """Admin-only view of exported archived-run logs from archived_run/results/."""
    user_id: int | None = session.get("user_id")
    if not user_id:
        return redirect(url_for("sessions_viewer.login_form"))

    if not session.get("is_admin"):
        return Response("Forbidden – admin access required", status=403)

    exports: list[dict[str, str | int | None]] = []
    if _ARCHIVED_RUN_RESULTS_DIR.is_dir():
        for export_dir in sorted(_ARCHIVED_RUN_RESULTS_DIR.iterdir(), reverse=True):
            if not export_dir.is_dir():
                continue

            sessions_path = export_dir / "sessions.json"
            if not sessions_path.exists():
                continue

            sessions_json: str | None = None
            session_count: int | None = None
            exported_at: str | None = None
            try:
                sessions_json = sessions_path.read_text(encoding="utf-8")
                parsed = json.loads(sessions_json)
                if isinstance(parsed, dict):
                    count = parsed.get("session_count")
                    if isinstance(count, int):
                        session_count = count
                    at = parsed.get("exported_at")
                    if isinstance(at, str):
                        exported_at = at
            except (OSError, json.JSONDecodeError):
                sessions_json = None

            exports.append(
                {
                    "export_id": export_dir.name,
                    "exported_at": exported_at or export_dir.name,
                    "session_count": session_count,
                    "sessions_json": sessions_json,
                }
            )

    return render_template(
        "archived_runs.html",
        exports=exports,
        user_id=user_id,
    )


@bp.post("/archived-runs/export")
def archived_runs_export() -> ResponseReturnValue:
    """Admin-only export of all archived run sessions into archived_run/results/."""
    user_id: int | None = session.get("user_id")
    if not user_id:
        return redirect(url_for("sessions_viewer.login_form"))

    if not session.get("is_admin"):
        return Response("Forbidden – admin access required", status=403)

    export_now = datetime.now(timezone.utc)

    db = get_db()
    rows = db.execute(
        "SELECT id, user_id, url, status, log, result_json, created_at, updated_at"
        " FROM sessions ORDER BY created_at DESC"
    ).fetchall()

    export_payload: dict[str, object] = {
        "exported_at": export_now.isoformat(),
        "exported_by_user_id": user_id,
        "session_count": len(rows),
        "sessions": [dict(row) for row in rows],
    }

    export_stamp = export_now.strftime("%Y-%m-%dT%H-%M-%S-%f")
    export_dir = _ARCHIVED_RUN_RESULTS_DIR / f"archived_runs__{export_stamp}"
    export_dir.mkdir(parents=True, exist_ok=False)
    (export_dir / "sessions.json").write_text(
        json.dumps(export_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return redirect(url_for("sessions_viewer.archived_runs_view"))
