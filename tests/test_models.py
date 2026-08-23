from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from src.db.models import Symbol, Kline, FetchLog, Scenario


def test_insert_symbol(db_session):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()
    result = db_session.get(Symbol, "BTCUSDT")
    assert result.base_asset == "BTC"
    assert result.is_active is True


def test_kline_unique_constraint_rejects_duplicates(db_session):
    open_time = datetime(2026, 1, 1, 0, 0, 0)
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=open_time,
        open=Decimal("100"), high=Decimal("110"), low=Decimal("90"),
        close=Decimal("105"), volume=Decimal("1000"), flagged=False,
    ))
    db_session.commit()

    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=open_time,
        open=Decimal("101"), high=Decimal("111"), low=Decimal("91"),
        close=Decimal("106"), volume=Decimal("1001"), flagged=False,
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_insert_fetch_log(db_session):
    now = datetime(2026, 1, 1, 12, 0, 0)
    db_session.add(FetchLog(
        symbol="BTCUSDT", timeframe="1h", started_at=now, finished_at=now,
        status="success", error_message=None,
    ))
    db_session.commit()
    row = db_session.query(FetchLog).first()
    assert row.status == "success"


def test_symbol_updated_at_refreshes_on_update(db_session):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()
    first = db_session.get(Symbol, "BTCUSDT").updated_at
    assert first is not None

    db_session.get(Symbol, "BTCUSDT").is_active = False
    db_session.commit()

    assert db_session.get(Symbol, "BTCUSDT").updated_at > first


def test_symbol_updated_at_uses_naive_utc_timestamps(db_session):
    """A tz-aware value in a naive DateTime column can shift on PostgreSQL."""
    from src.timeutil import utc_now

    # SQLAlchemy wraps zero-arg callables, so compare against the wrapped one.
    column = Symbol.__table__.c.updated_at
    assert column.default.arg.__wrapped__ is utc_now
    assert column.onupdate.arg.__wrapped__ is utc_now

    # What the column would actually store must be naive, at both ends.
    assert column.default.arg(None).tzinfo is None
    assert column.onupdate.arg(None).tzinfo is None

    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()
    assert db_session.get(Symbol, "BTCUSDT").updated_at.tzinfo is None


def test_insert_scenario(db_session):
    now = datetime(2026, 1, 1, 12, 0, 0)
    db_session.add(Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("50000"), target_price=Decimal("52000"), stop_price=Decimal("49000"),
        expected_return_pct=Decimal("0.04"), confidence_score=Decimal("0.7"),
        created_at=now, expires_at=now + timedelta(hours=24), status="pending",
    ))
    db_session.commit()
    row = db_session.query(Scenario).first()
    assert row.symbol == "BTCUSDT"
    assert row.direction == "long"
    assert row.status == "pending"


def test_scenario_status_defaults_to_pending(db_session):
    now = datetime(2026, 1, 1, 12, 0, 0)
    db_session.add(Scenario(
        symbol="ETHUSDT", direction="short",
        entry_price=Decimal("3000"), target_price=Decimal("2900"), stop_price=Decimal("3050"),
        expected_return_pct=Decimal("0.033"), confidence_score=Decimal("0.5"),
        created_at=now, expires_at=now + timedelta(hours=24),
    ))
    db_session.commit()
    row = db_session.query(Scenario).filter(Scenario.symbol == "ETHUSDT").first()
    assert row.status == "pending"


def test_scenario_resolved_at_and_calibrated_confidence_default_to_null(db_session):
    now = datetime(2026, 1, 1, 12, 0, 0)
    db_session.add(Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("50000"), target_price=Decimal("52000"), stop_price=Decimal("49000"),
        expected_return_pct=Decimal("0.04"), confidence_score=Decimal("0.7"),
        created_at=now, expires_at=now + timedelta(hours=24), status="pending",
    ))
    db_session.commit()
    row = db_session.query(Scenario).first()
    assert row.resolved_at is None
    assert row.calibrated_confidence is None

    row.status = "hit_target"
    row.resolved_at = now + timedelta(hours=3)
    row.calibrated_confidence = Decimal("0.65")
    db_session.commit()

    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "hit_target"
    assert reloaded.resolved_at == now + timedelta(hours=3)
    assert reloaded.calibrated_confidence == Decimal("0.65")


def test_paper_position_defaults_and_unique_scenario_constraint(db_session):
    from src.db.models import PaperPosition

    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("50000"), target_price=Decimal("52000"), stop_price=Decimal("49000"),
        expected_return_pct=Decimal("0.04"), confidence_score=Decimal("0.7"),
        created_at=now, expires_at=now + timedelta(hours=24), status="pending",
    )
    db_session.add(scenario)
    db_session.commit()

    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol="BTCUSDT", direction="long",
        entry_price=Decimal("50000"), stop_price=Decimal("49000"), target_price=Decimal("52000"),
        risk_amount=Decimal("100"), position_size=Decimal("0.1"),
        opened_at=now, status="open",
    ))
    db_session.commit()

    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.status == "open"
    assert reloaded.closed_at is None
    assert reloaded.exit_price is None
    assert reloaded.realized_pnl is None
    assert reloaded.equity_before is None
    assert reloaded.equity_after is None

    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol="BTCUSDT", direction="long",
        entry_price=Decimal("50000"), stop_price=Decimal("49000"), target_price=Decimal("52000"),
        risk_amount=Decimal("100"), position_size=Decimal("0.1"),
        opened_at=now, status="open",
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
