from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from src.binance_client import BinanceClient
from src.db.models import Kline
from src.kline_fetcher import FetchResult
from src.timeutil import to_epoch_ms
import src.backfill as backfill_module


class _FakeBinanceRest:
    """Behaves like Binance's REST kline endpoint: returns the candles whose
    open_time falls inside [startTime, endTime] (both inclusive), so the real
    BinanceClient pagination loop is exercised rather than mocked away."""

    def __init__(self, candles):
        self._candles = candles
        self.calls = []

    def get_klines(self, symbol, interval, startTime, endTime, limit):
        self.calls.append({"startTime": startTime, "endTime": endTime})
        return [c for c in self._candles if startTime <= c[0] <= endTime][:limit]


def _kline(symbol, timeframe, open_time):
    return Kline(
        symbol=symbol, timeframe=timeframe, open_time=open_time,
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
        close=Decimal("1"), volume=Decimal("1"), flagged=False,
    )


def test_run_initial_backfill_calls_fetch_and_store_per_symbol_and_timeframe(db_session):
    calls = []

    def fake_process(session, client, symbol, timeframe, start_ms, end_ms):
        calls.append((symbol, timeframe))
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    with patch.object(backfill_module, "process_symbol_timeframe", side_effect=fake_process):
        results = backfill_module.run_initial_backfill(
            db_session, binance_client=object(), symbols=["BTCUSDT", "ETHUSDT"], timeframes=["1h", "1d"],
        )
    assert set(calls) == {("BTCUSDT", "1h"), ("BTCUSDT", "1d"), ("ETHUSDT", "1h"), ("ETHUSDT", "1d")}
    assert len(results) == 4


def test_run_initial_backfill_passes_utc_epoch_millisecond_window(db_session):
    captured = []

    def fake_process(session, client, symbol, timeframe, start_ms, end_ms):
        captured.append({"start_ms": start_ms, "end_ms": end_ms})
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    frozen_now = datetime(2026, 1, 2, 0, 0, 0)
    with patch.object(backfill_module, "process_symbol_timeframe", side_effect=fake_process), \
         patch.object(backfill_module, "utc_now", return_value=frozen_now):
        backfill_module.run_initial_backfill(
            db_session, binance_client=object(), symbols=["BTCUSDT"], timeframes=["1h"], since_days=1,
        )

    assert captured == [{"start_ms": 1767225600000, "end_ms": 1767312000000}]


def test_run_initial_backfill_isolates_symbol_failures_and_continues(db_session):
    """A DB failure for one symbol must not poison the session for the rest."""

    class _PartiallyFailingClient:
        def get_klines(self, symbol, timeframe, start_ms, end_ms):
            close = None if symbol == "FAILSYMBOL" else Decimal("100")  # None -> NOT NULL violation
            return [{
                "open_time": datetime(2026, 1, 1, 0),
                "open": Decimal("100"), "high": Decimal("100"), "low": Decimal("100"),
                "close": close, "volume": Decimal("1000"),
            }]

    results = backfill_module.run_initial_backfill(
        db_session, _PartiallyFailingClient(), symbols=["FAILSYMBOL", "OKSYMBOL"], timeframes=["1h"],
    )

    by_symbol = {result.symbol: result for result in results}
    assert by_symbol["FAILSYMBOL"].error is not None
    assert by_symbol["OKSYMBOL"].error is None
    assert by_symbol["OKSYMBOL"].inserted == 1
    assert db_session.query(Kline).filter(Kline.symbol == "OKSYMBOL").count() == 1


def test_run_gap_backfill_fetches_only_missing_ranges(db_session):
    db_session.add(_kline("BTCUSDT", "1h", datetime(2026, 1, 1, 0)))
    db_session.add(_kline("BTCUSDT", "1h", datetime(2026, 1, 1, 2)))
    db_session.commit()

    calls = []

    def fake_process(session, client, symbol, timeframe, start_ms, end_ms):
        calls.append((start_ms, end_ms))
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    with patch.object(backfill_module, "process_symbol_timeframe", side_effect=fake_process):
        results = backfill_module.run_gap_backfill(
            db_session, binance_client=object(), symbol="BTCUSDT", timeframe="1h",
            range_start=datetime(2026, 1, 1, 0), range_end=datetime(2026, 1, 1, 2),
        )
    assert len(results) == 1
    assert len(calls) == 1


def test_run_gap_backfill_end_bound_is_inclusive_of_last_missing_candle(db_session):
    db_session.add(_kline("BTCUSDT", "1h", datetime(2026, 1, 1, 0)))
    db_session.add(_kline("BTCUSDT", "1h", datetime(2026, 1, 1, 2)))
    db_session.commit()

    calls = []

    def fake_process(session, client, symbol, timeframe, start_ms, end_ms):
        calls.append((start_ms, end_ms))
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    with patch.object(backfill_module, "process_symbol_timeframe", side_effect=fake_process):
        backfill_module.run_gap_backfill(
            db_session, binance_client=object(), symbol="BTCUSDT", timeframe="1h",
            range_start=datetime(2026, 1, 1, 0), range_end=datetime(2026, 1, 1, 2),
        )

    missing = datetime(2026, 1, 1, 1)
    assert calls == [(to_epoch_ms(missing), to_epoch_ms(missing + timedelta(hours=1)))]


def test_run_gap_backfill_repairs_single_candle_gap_through_real_client(db_session):
    """Regression: start_ms == end_ms made BinanceClient's `while cursor <
    end_ms` loop return [] and report the gap as repaired with zero rows."""
    hours = [datetime(2026, 1, 1, h) for h in range(3)]
    candles = [[to_epoch_ms(t), "100", "110", "90", "105", "1000"] for t in hours]

    db_session.add(_kline("BTCUSDT", "1h", hours[0]))
    db_session.add(_kline("BTCUSDT", "1h", hours[2]))
    db_session.commit()

    fake_rest = _FakeBinanceRest(candles)
    client = BinanceClient(client=fake_rest)

    results = backfill_module.run_gap_backfill(
        db_session, client, symbol="BTCUSDT", timeframe="1h",
        range_start=hours[0], range_end=hours[2],
    )

    assert len(results) == 1
    assert results[0].error is None
    assert results[0].fetched >= 1
    stored = {row.open_time for row in db_session.query(Kline).filter(Kline.symbol == "BTCUSDT").all()}
    assert hours[1] in stored, "the single missing candle was never fetched"
