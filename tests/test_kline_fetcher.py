from datetime import datetime
from decimal import Decimal

from src.kline_fetcher import fetch_and_store, process_symbol_timeframe


class _FakeBinanceClient:
    def __init__(self, rows=None, error=None):
        self._rows = rows or []
        self._error = error

    def get_klines(self, symbol, timeframe, start_ms, end_ms):
        if self._error:
            raise self._error
        return self._rows


def _row(open_time, close, volume="1000"):
    return {"open_time": open_time, "open": Decimal("100"), "high": Decimal("100"),
            "low": Decimal("100"), "close": Decimal(close), "volume": Decimal(volume)}


def test_fetch_and_store_persists_rows_and_reports_counts(db_session):
    rows = [_row(datetime(2026, 1, 1, 0), "100"), _row(datetime(2026, 1, 1, 1), "160")]
    fake = _FakeBinanceClient(rows=rows)
    result = fetch_and_store(db_session, fake, "BTCUSDT", "1h", start_ms=0, end_ms=1)
    assert result.fetched == 2
    assert result.inserted == 2
    assert result.updated == 0
    assert result.flagged == 1  # the 60% spike
    assert result.error is None


def test_fetch_and_store_captures_error_without_raising(db_session):
    fake = _FakeBinanceClient(error=RuntimeError("network down"))
    result = fetch_and_store(db_session, fake, "BTCUSDT", "1h", start_ms=0, end_ms=1)
    assert result.error == "network down"
    assert result.fetched == 0


def test_fetch_and_store_captures_storage_error_without_raising(db_session, monkeypatch):
    rows = [_row(datetime(2026, 1, 1, 0), "100"), _row(datetime(2026, 1, 1, 1), "160")]
    fake = _FakeBinanceClient(rows=rows)
    # Make upsert_klines raise an error to simulate storage-layer failure
    monkeypatch.setattr(
        "src.kline_fetcher.upsert_klines",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("DB constraint violation"))
    )
    result = fetch_and_store(db_session, fake, "BTCUSDT", "1h", start_ms=0, end_ms=1)
    assert result.error == "DB constraint violation"
    assert result.fetched == 0
    assert result.inserted == 0
    assert result.updated == 0
    assert result.flagged == 0


def test_process_symbol_timeframe_returns_result_on_success(db_session):
    rows = [_row(datetime(2026, 1, 1, 0), "100")]
    result = process_symbol_timeframe(db_session, _FakeBinanceClient(rows=rows), "BTCUSDT", "1h", 0, 1)
    assert result.error is None
    assert result.inserted == 1


def test_process_symbol_timeframe_rolls_back_dirty_session_on_failure(db_session):
    """Without the rollback the session stays in pending-rollback state and
    every later symbol in the batch fails too."""
    bad_row = {**_row(datetime(2026, 1, 1, 0), "100"), "close": None}  # NOT NULL violation
    failing = _FakeBinanceClient(rows=[bad_row])

    result = process_symbol_timeframe(db_session, failing, "FAILSYMBOL", "1h", 0, 1)
    assert result.error is not None

    # The session must be usable again straight away.
    ok = process_symbol_timeframe(
        db_session, _FakeBinanceClient(rows=[_row(datetime(2026, 1, 1, 0), "100")]),
        "OKSYMBOL", "1h", 0, 1,
    )
    assert ok.error is None
    assert ok.inserted == 1


def test_fetch_and_store_with_empty_rows_no_lookup(db_session):
    fake = _FakeBinanceClient(rows=[])
    result = fetch_and_store(db_session, fake, "BTCUSDT", "1h", start_ms=0, end_ms=1)
    assert result.fetched == 0
    assert result.inserted == 0
    assert result.updated == 0
    assert result.flagged == 0
    assert result.error is None


def test_fetch_and_store_with_second_fetch_spike_flagged(db_session, monkeypatch):
    # First fetch - store some data
    first_rows = [_row(datetime(2026, 1, 1, 0), "100")]
    fake_first = _FakeBinanceClient(rows=first_rows)
    result_first = fetch_and_store(db_session, fake_first, "BTCUSDT", "1h", start_ms=0, end_ms=1)
    assert result_first.fetched == 1
    assert result_first.inserted == 1

    # Second fetch - with a spike that should be flagged
    second_rows = [_row(datetime(2026, 1, 1, 1), "200")]  # 100% spike
    fake_second = _FakeBinanceClient(rows=second_rows)
    
    # Mock get_last_close_before to return the close from first row (100)
    monkeypatch.setattr(
        "src.kline_fetcher.get_last_close_before",
        lambda session, symbol, timeframe, open_time: Decimal("100")
    )
    
    result_second = fetch_and_store(db_session, fake_second, "BTCUSDT", "1h", start_ms=0, end_ms=1)
    assert result_second.fetched == 1
    assert result_second.flagged == 1  # Should be flagged due to 100% spike
    assert result_second.error is None