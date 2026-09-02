from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from src.db.models import (
    FetchLog,
    PaperPosition,
    PortfolioPosition,
    PortfolioSnapshot,
    Scenario,
)
from src.portfolio.config import STRATEGY_VERSION
from src.timeutil import utc_now
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


def test_dashboard_shows_each_leg_separately(db_session):
    # The two legs take turns -- the long leg carried the book in 2023-2024 and
    # the short leg in 2025-2026. A book whose legs cancel out reads as "flat"
    # in the net figure and is indistinguishable there from a book whose legs
    # are both dead, so the page has to show them apart.
    def _closed(symbol, direction, gross, fee, funding):
        db_session.add(PortfolioPosition(
            strategy_version=STRATEGY_VERSION, symbol=symbol, direction=direction,
            entry_price=Decimal("100"), position_size=Decimal("2"),
            opened_at=datetime(2026, 1, 1), closed_at=datetime(2026, 1, 8),
            exit_price=Decimal("110"), status="closed",
            gross_pnl=Decimal(gross), fee_cost=Decimal(fee),
            funding_cost=Decimal(funding),
            realized_pnl=Decimal(gross) - Decimal(fee) - Decimal(funding),
        ))

    _closed("AAAUSDT", "long", 300, 20, -50)     # kazanan bacak, funding tahsil
    _closed("BBBUSDT", "short", -280, 20, 10)    # kaybeden bacak
    db_session.commit()

    body = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD)).get_data(as_text=True)
    kart = body.split("Defterdeki Pozisyonlar")[0]

    assert "+330.00" in kart          # long: 300 - 20 - (-50)
    assert "-310.00" in kart          # short: -280 - 20 - 10
    # Net toplam +20; iki bacagin -310 ve +330 oldugu bilgisi olmadan bu rakam
    # "sakin bir hafta" gibi okunur.
    assert "20.00" in kart


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


def test_health_needs_no_credentials(db_session):
    # A platform health check cannot send an Authorization header. If this
    # route ever starts demanding one, the deploy goes unhealthy and the
    # service restarts in a loop.
    assert _client(db_session).get("/health").status_code in (200, 503)


def test_health_reports_stopped_when_nothing_has_been_fetched(db_session):
    response = _client(db_session).get("/health")
    assert response.status_code == 503
    assert response.get_json()["status"] == "stopped"


def test_health_reports_healthy_after_a_recent_fetch(db_session):
    now = utc_now()
    db_session.add(FetchLog(
        symbol="BTCUSDT", timeframe="1h", started_at=now, finished_at=now,
        status="success",
    ))
    db_session.commit()

    response = _client(db_session).get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "healthy"


def test_health_survives_a_broken_query(db_session):
    with patch("src.web.get_system_health", side_effect=RuntimeError("db down")):
        response = _client(db_session).get("/health")

    assert response.status_code == 503
    assert response.get_json()["status"] == "error"
