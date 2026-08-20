from datetime import datetime
from decimal import Decimal

from src.db.models import Symbol
from src.storage import upsert_symbols, mark_symbols_inactive, upsert_klines


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
