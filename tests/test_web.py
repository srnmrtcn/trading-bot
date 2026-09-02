from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from src.db.models import PaperPosition, PortfolioPosition, PortfolioSnapshot, Scenario
from src.portfolio.config import STRATEGY_VERSION
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


def test_dashboard_rejects_non_basic_auth_scheme(db_session):
    response = _client(db_session).get("/", headers={"Authorization": 'Digest username="admin"'})
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


def test_dashboard_shows_the_momentum_book(db_session):
    db_session.add(PortfolioPosition(
        strategy_version=STRATEGY_VERSION, symbol="ETHUSDT", direction="short",
        entry_price=Decimal("2000"), position_size=Decimal("3"),
        opened_at=datetime(2026, 1, 1), status="open",
    ))
    db_session.add(PortfolioSnapshot(
        strategy_version=STRATEGY_VERSION, as_of=datetime(2026, 1, 1),
        equity=Decimal("11500"),
    ))
    db_session.commit()

    body = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD)).get_data(as_text=True)

    assert "Momentum Defteri" in body
    assert "ETHUSDT" in body
    assert "11500.00" in body


def test_dashboard_book_does_not_borrow_the_paper_path_equity(db_session):
    # Both strategies keep an equity figure. Rendering one under the other's
    # heading would be invisible in the numbers and wrong in every conclusion
    # drawn from them, so the book shows its own starting value when it has no
    # snapshots of its own -- not whatever the paper path is worth.
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=datetime(2026, 1, 1), expires_at=datetime(2026, 1, 2), status="hit_target",
    )
    db_session.add(scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), closed_at=datetime(2026, 1, 2), status="closed",
        realized_pnl=Decimal("77"), equity_after=Decimal("10077"),
    ))
    db_session.commit()

    body = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD)).get_data(as_text=True)

    assert "Defter şu an boş" in body
    assert "10077" not in body.split("Senaryo Motoru — Açık Pozisyonlar")[0].split("Momentum Defteri")[1]
