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
from src.portfolio.dashboard import book_performance, open_book, portfolio_equity_history

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

    @app.route("/health")
    def health():
        """Liveness probe. Deliberately unauthenticated and deliberately thin.

        A platform health check cannot carry credentials, so this route has
        none -- which is why it answers with a single word and no numbers. It
        reports whether the hourly job is still writing to fetch_log, and
        nothing about equity, positions or symbols.

        A stalled scheduler answers 503 rather than 200. A process that is
        alive but no longer fetching is the failure this exists to catch;
        answering 200 because Flask is still up would hide exactly that.
        """
        session = session_factory()
        try:
            status = get_system_health(session).status
            return {"status": status}, 200 if status != "stopped" else 503
        except Exception:
            logger.exception("Health check failed")
            return {"status": "error"}, 503
        finally:
            session.close()

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
            # The two strategies are rendered side by side but never mixed:
            # separate tables, separate equity, separate strategy versions.
            book = book_performance(session)
            book_history = portfolio_equity_history(session)
            return render_template(
                "dashboard.html",
                error=False,
                health=health,
                equity=equity,
                sparkline_points=equity_sparkline_points(equity.history),
                open_positions=open_positions,
                recent_scenarios=recent_scenarios,
                book=book,
                book_positions=open_book(session),
                book_sparkline_points=equity_sparkline_points(book_history),
            )
        except Exception:
            logger.exception("Failed to load dashboard data")
            return render_template("dashboard.html", error=True)
        finally:
            session.close()

    return app
