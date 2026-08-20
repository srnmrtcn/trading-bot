from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from src.db.models import Kline
from src.kline_fetcher import FetchResult
import src.backfill as backfill_module


def test_run_initial_backfill_calls_fetch_and_store_per_symbol_and_timeframe(db_session):
    calls = []

    def fake_fetch_and_store(session, client, symbol, timeframe, start_ms, end_ms):
        calls.append((symbol, timeframe))
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    with patch.object(backfill_module, "fetch_and_store", side_effect=fake_fetch_and_store):
        results = backfill_module.run_initial_backfill(
            db_session, binance_client=object(), symbols=["BTCUSDT", "ETHUSDT"], timeframes=["1h", "1d"],
        )
    assert set(calls) == {("BTCUSDT", "1h"), ("BTCUSDT", "1d"), ("ETHUSDT", "1h"), ("ETHUSDT", "1d")}
    assert len(results) == 4


def test_run_gap_backfill_fetches_only_missing_ranges(db_session):
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=datetime(2026, 1, 1, 0),
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"),
        volume=Decimal("1"), flagged=False,
    ))
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=datetime(2026, 1, 1, 2),
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"),
        volume=Decimal("1"), flagged=False,
    ))
    db_session.commit()

    calls = []

    def fake_fetch_and_store(session, client, symbol, timeframe, start_ms, end_ms):
        calls.append((start_ms, end_ms))
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    with patch.object(backfill_module, "fetch_and_store", side_effect=fake_fetch_and_store):
        results = backfill_module.run_gap_backfill(
            db_session, binance_client=object(), symbol="BTCUSDT", timeframe="1h",
            range_start=datetime(2026, 1, 1, 0), range_end=datetime(2026, 1, 1, 2),
        )
    assert len(results) == 1
    assert len(calls) == 1
