from datetime import datetime
from decimal import Decimal

from src.kline_fetcher import fetch_and_store


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
