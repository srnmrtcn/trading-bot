from datetime import datetime
from decimal import Decimal

from src.db.models import Symbol
from src.storage import get_kline_time_bounds, upsert_symbols, mark_symbols_inactive, upsert_klines, get_last_close_before


def test_upsert_symbols_inserts_new(db_session):
    upsert_symbols(db_session, [
        {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT", "listed_at": None},
    ])
    result = db_session.get(Symbol, "BTCUSDT")
    assert result is not None
    assert result.is_active is True


def test_upsert_symbols_reactivates_existing(db_session):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=False))
    db_session.commit()
    upsert_symbols(db_session, [
        {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT", "listed_at": None},
    ])
    assert db_session.get(Symbol, "BTCUSDT").is_active is True


def test_mark_symbols_inactive_deactivates_missing(db_session):
    db_session.add_all([
        Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True),
        Symbol(symbol="ETHUSDT", base_asset="ETH", quote_asset="USDT", is_active=True),
    ])
    db_session.commit()
    mark_symbols_inactive(db_session, active_symbols={"BTCUSDT"})
    assert db_session.get(Symbol, "BTCUSDT").is_active is True
    assert db_session.get(Symbol, "ETHUSDT").is_active is False


def _row(open_time, close):
    return {
        "open_time": open_time, "open": Decimal("100"), "high": Decimal("110"),
        "low": Decimal("90"), "close": close, "volume": Decimal("1000"), "flagged": False,
    }


def test_upsert_klines_inserts_new_rows(db_session):
    rows = [_row(datetime(2026, 1, 1, 0), Decimal("105")), _row(datetime(2026, 1, 1, 1), Decimal("106"))]
    result = upsert_klines(db_session, "BTCUSDT", "1h", rows)
    assert result.inserted == 2
    assert result.updated == 0


def test_upsert_klines_updates_existing_rows_without_duplicating(db_session):
    open_time = datetime(2026, 1, 1, 0)
    upsert_klines(db_session, "BTCUSDT", "1h", [_row(open_time, Decimal("105"))])
    result = upsert_klines(db_session, "BTCUSDT", "1h", [_row(open_time, Decimal("999"))])
    assert result.inserted == 0
    assert result.updated == 1

    from src.db.models import Kline
    rows_in_db = db_session.query(Kline).filter(Kline.symbol == "BTCUSDT", Kline.timeframe == "1h").all()
    assert len(rows_in_db) == 1
    assert rows_in_db[0].close == Decimal("999")


def test_get_kline_time_bounds_returns_none_when_no_rows(db_session):
    assert get_kline_time_bounds(db_session, "BTCUSDT", "1h") == (None, None)


def test_get_kline_time_bounds_returns_earliest_and_latest_open_time(db_session):
    upsert_klines(db_session, "BTCUSDT", "1h", [
        _row(datetime(2026, 1, 1, 3), Decimal("105")),
        _row(datetime(2026, 1, 1, 1), Decimal("105")),
        _row(datetime(2026, 1, 1, 2), Decimal("105")),
    ])
    # A different symbol/timeframe must not influence the bounds.
    upsert_klines(db_session, "ETHUSDT", "1h", [_row(datetime(2026, 5, 1, 0), Decimal("105"))])
    upsert_klines(db_session, "BTCUSDT", "1d", [_row(datetime(2020, 1, 1, 0), Decimal("105"))])

    assert get_kline_time_bounds(db_session, "BTCUSDT", "1h") == (
        datetime(2026, 1, 1, 1), datetime(2026, 1, 1, 3),
    )


def test_get_last_close_before_returns_correct_value(db_session):
    # Setup test data with specific times
    upsert_klines(db_session, "BTCUSDT", "1h", [
        _row(datetime(2026, 1, 1, 1), Decimal("105")),
        _row(datetime(2026, 1, 1, 2), Decimal("106")),
        _row(datetime(2026, 1, 1, 3), Decimal("107")),
    ])
    
    # Test case: get last close before time 2026-01-01 2:00 -> should return 105 (from 1:00)
    result = get_last_close_before(db_session, "BTCUSDT", "1h", datetime(2026, 1, 1, 2))
    assert result == Decimal("105")

    # Test case: get last close before time 2026-01-01 3:00 -> should return 106 (from 2:00)
    result = get_last_close_before(db_session, "BTCUSDT", "1h", datetime(2026, 1, 1, 3))
    assert result == Decimal("106")

    # Test case: get last close before time 2026-01-01 4:00 -> should return 107 (from 3:00)
    result = get_last_close_before(db_session, "BTCUSDT", "1h", datetime(2026, 1, 1, 4))
    assert result == Decimal("107")

    # Test case: get last close before time 2026-01-01 0:00 -> should return None (no previous data)
    result = get_last_close_before(db_session, "BTCUSDT", "1h", datetime(2026, 1, 1, 0))
    assert result is None

    # Test case: different symbol should not affect results
    upsert_klines(db_session, "ETHUSDT", "1h", [_row(datetime(2026, 1, 1, 1), Decimal("200"))])
    result = get_last_close_before(db_session, "BTCUSDT", "1h", datetime(2026, 1, 1, 2))
    assert result == Decimal("105")

    # Test case: different timeframe should not affect results
    upsert_klines(db_session, "BTCUSDT", "1d", [_row(datetime(2026, 1, 1, 0), Decimal("300"))])
    result = get_last_close_before(db_session, "BTCUSDT", "1h", datetime(2026, 1, 1, 2))
    assert result == Decimal("105")