from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from src.db.models import Symbol, Kline, FetchLog


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
