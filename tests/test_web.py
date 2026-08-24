from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from src.db.models import PaperPosition, Scenario
from src.web import create_app

AUTH_USER = "admin"
AUTH_PASSWORD = "s3cret"
AUTH_PASS_HASH = generate_password_hash(AUTH_PASSWORD, method="pbkdf2:sha256")


def _client(db_session):
    app = create_app(lambda: db_session, AUTH_USER, AUTH_PASS_HASH)
    return app.test_client()


def test_dashboard_requires_auth(db_session):
    response = _client(db_session).get("/")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


def test_dashboard_rejects_wrong_password(db_session):
    response = _client(db_session).get("/", auth=(AUTH_USER, "wrong"))
    assert response.status_code == 401


def test_dashboard_rejects_wrong_username(db_session):
    response = _client(db_session).get("/", auth=("nobody", AUTH_PASSWORD))
    assert response.status_code == 401


def test_dashboard_renders_with_correct_credentials(db_session):
    response = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD))
    assert response.status_code == 200
    assert "Dashboard" in response.get_data(as_text=True)


def test_dashboard_shows_an_open_position(db_session):
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=datetime(2026, 1, 1), expires_at=datetime(2026, 1, 2), status="pending",
    )
    db_session.add(scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
    ))
    db_session.commit()

    response = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD))

    assert "BTCUSDT" in response.get_data(as_text=True)


def test_dashboard_shows_empty_state_with_no_data(db_session):
    response = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD))
    body = response.get_data(as_text=True)
    assert "Açık pozisyon yok" in body
    assert "Henüz senaryo yok" in body


def test_dashboard_renders_error_message_when_a_query_fails(db_session):
    with patch("src.web.get_system_health", side_effect=RuntimeError("db down")):
        response = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD))

    assert response.status_code == 200
    assert "okunamıyor" in response.get_data(as_text=True)
