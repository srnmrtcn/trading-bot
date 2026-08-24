from __future__ import annotations

import logging

from flask import Flask, Response, render_template, request
from werkzeug.security import check_password_hash

from src.dashboard_data import (
    equity_sparkline_points,
    get_equity_summary,
    get_open_positions,
    get_recent_scenarios,
    get_system_health,
)

logger = logging.getLogger("web")


def create_app(session_factory, auth_user: str, auth_pass_hash: str) -> Flask:
    app = Flask(__name__)

    def _authorized() -> bool:
        auth = request.authorization
        return (
            auth is not None
            and auth.type == "basic"
            and auth.username == auth_user
            and auth.password is not None
            and check_password_hash(auth_pass_hash, auth.password)
        )

    @app.route("/")
    def dashboard():
        if not _authorized():
            return Response(
                "Authentication required", 401,
                {"WWW-Authenticate": 'Basic realm="Dashboard"'},
            )

        session = session_factory()
        try:
            health = get_system_health(session)
            equity = get_equity_summary(session)
            open_positions = get_open_positions(session)
            recent_scenarios = get_recent_scenarios(session)
            return render_template(
                "dashboard.html",
                error=False,
                health=health,
                equity=equity,
                sparkline_points=equity_sparkline_points(equity.history),
                open_positions=open_positions,
                recent_scenarios=recent_scenarios,
            )
        except Exception:
            logger.exception("Failed to load dashboard data")
            return render_template("dashboard.html", error=True)
        finally:
            session.close()

    return app
