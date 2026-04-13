"""Session viewer routes.

POST /sessions/login  – accepts JSON {token: ...} or form data, sets signed cookie
GET /sessions         – session viewer protected by signed cookie
GET /sessions/login   – show the login form
"""

from flask import Blueprint, Response, redirect, render_template, request, session, url_for
from flask.typing import ResponseReturnValue

from app.models.user import get_user_by_token

bp = Blueprint("sessions_viewer", __name__)


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
    return redirect(url_for("sessions_viewer.sessions_view"))


@bp.get("/sessions")
def sessions_view() -> ResponseReturnValue:
    """Show sessions for the authenticated user."""
    user_id: int | None = session.get("user_id")
    if not user_id:
        return redirect(url_for("sessions_viewer.login_form"))

    from app.models.session import get_sessions_for_user

    user_sessions = get_sessions_for_user(user_id)
    return render_template("sessions.html", sessions=user_sessions, user_id=user_id)
