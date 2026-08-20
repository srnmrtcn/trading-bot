import logging
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FetchLog, Kline, Symbol
from src.integrity import floor_to_timeframe
from src.scheduler import (
    build_scheduler,
    get_resume_point,
    run_symbol_refresh_job,
    run_timeframe_job,
)
from src.timeutil import to_epoch_ms, utc_now


class _FakeBinanceClient:
    def __init__(self):
        self.symbols_requested = []
        self.calls = []

    def get_active_usdt_symbols(self):
        return [{"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"}]

    def get_klines(self, symbol, timeframe, start_ms, end_ms):
        self.symbols_requested.append(symbol)
        self.calls.append({"symbol": symbol, "start_ms": start_ms, "end_ms": end_ms})
        return []


class _PartiallyFailingBinanceClient:
    """Returns a row for FAILSYMBOL that will blow up the DB commit inside
    storage.upsert_klines (NOT NULL violation on `close`), leaving the
    SQLAlchemy session dirty. All other symbols succeed with an empty batch."""

    def __init__(self):
        self.symbols_requested = []

    def get_klines(self, symbol, timeframe, start_ms, end_ms):
        self.symbols_requested.append(symbol)
        if symbol == "FAILSYMBOL":
            return [{
                "open_time": datetime(2026, 1, 1, 0),
                "open": Decimal("100"),
                "high": Decimal("100"),
                "low": Decimal("100"),
                "close": None,  # violates Kline.close NOT NULL -> commit fails
                "volume": Decimal("1000"),
            }]
        return []


def _kline(symbol, timeframe, open_time, close="1"):
    return Kline(
        symbol=symbol, timeframe=timeframe, open_time=open_time,
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
        close=Decimal(close), volume=Decimal("1"), flagged=False,
    )


def test_build_scheduler_registers_expected_jobs():
    scheduler = build_scheduler(session_factory=lambda: None, binance_client=None)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {"hourly_klines", "daily_klines", "symbol_refresh"}


def test_run_timeframe_job_fetches_active_symbols_and_records_log(db_session):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()
    fake_client = _FakeBinanceClient()

    run_timeframe_job(session_factory=lambda: db_session, binance_client=fake_client, timeframe="1h")

    assert fake_client.symbols_requested == ["BTCUSDT"]
    log_row = db_session.query(FetchLog).first()
    assert log_row.symbol == "BTCUSDT"
    assert log_row.status == "success"


def test_run_symbol_refresh_job_upserts_symbols(db_session):
    fake_client = _FakeBinanceClient()
    run_symbol_refresh_job(session_factory=lambda: db_session, binance_client=fake_client)
    assert db_session.get(Symbol, "BTCUSDT") is not None


def test_run_timeframe_job_isolates_symbol_failures_and_continues(db_session):
    db_session.add(Symbol(symbol="FAILSYMBOL", base_asset="FAIL", quote_asset="USDT", is_active=True))
    db_session.add(Symbol(symbol="OKSYMBOL", base_asset="OK", quote_asset="USDT", is_active=True))
    db_session.commit()
    fake_client = _PartiallyFailingBinanceClient()

    # Must not raise, even though FAILSYMBOL's commit fails inside storage
    # and would otherwise leave the session dirty for the next symbol.
    run_timeframe_job(session_factory=lambda: db_session, binance_client=fake_client, timeframe="1h")

    assert set(fake_client.symbols_requested) == {"FAILSYMBOL", "OKSYMBOL"}
    logs = {row.symbol: row.status for row in db_session.query(FetchLog).all()}
    assert logs.get("OKSYMBOL") == "success"
    assert logs.get("FAILSYMBOL") == "error"


def test_get_resume_point_uses_last_stored_kline_not_fetch_log(db_session):
    """The watermark must come from the data, never from fetch_log wall-clock."""
    db_session.add(_kline("BTCUSDT", "1h", datetime(2026, 1, 1, 5)))
    db_session.add(FetchLog(
        symbol="BTCUSDT", timeframe="1h", status="success",
        started_at=datetime(2026, 3, 1, 0), finished_at=datetime(2026, 3, 1, 0, 42),
    ))
    db_session.commit()

    assert get_resume_point(db_session, "BTCUSDT", "1h", now=datetime(2026, 6, 1)) == datetime(2026, 1, 1, 5)


def test_get_resume_point_falls_back_to_730_days_when_no_data(db_session):
    now = datetime(2026, 6, 1)
    assert get_resume_point(db_session, "BTCUSDT", "1h", now=now) == now - timedelta(days=730)


def test_run_timeframe_job_start_derives_from_kline_not_fetch_log(db_session):
    """Regression: using FetchLog.finished_at as the window start silently
    skipped every candle between the fetch's end and the run's finish time."""
    last_stored = datetime(2026, 1, 1, 5)
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.add(_kline("BTCUSDT", "1h", last_stored))
    db_session.add(FetchLog(
        symbol="BTCUSDT", timeframe="1h", status="success",
        started_at=datetime(2026, 3, 1, 0), finished_at=datetime(2026, 3, 1, 0, 42),
    ))
    db_session.commit()
    fake_client = _FakeBinanceClient()

    run_timeframe_job(session_factory=lambda: db_session, binance_client=fake_client, timeframe="1h")

    assert fake_client.calls[0]["start_ms"] == to_epoch_ms(last_stored)
    assert fake_client.calls[0]["start_ms"] != to_epoch_ms(datetime(2026, 3, 1, 0, 42))


def test_run_timeframe_job_repairs_gaps_in_stored_data(db_session):
    """Gap repair must be reachable from the job entrypoint, not just callable."""
    hour = floor_to_timeframe(utc_now(), "1h")
    stored = [hour - timedelta(hours=n) for n in (5, 4, 3, 1, 0)]  # hour-2 missing
    missing = hour - timedelta(hours=2)

    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    for open_time in stored:
        db_session.add(_kline("BTCUSDT", "1h", open_time))
    db_session.commit()
    fake_client = _FakeBinanceClient()

    run_timeframe_job(
        session_factory=lambda: db_session, binance_client=fake_client,
        timeframe="1h", now=hour + timedelta(minutes=5),
    )

    gap_calls = [
        call for call in fake_client.calls
        if call["start_ms"] == to_epoch_ms(missing)
        and call["end_ms"] == to_epoch_ms(missing + timedelta(hours=1))
    ]
    assert gap_calls, f"gap at {missing} was never fetched; calls={fake_client.calls}"


def test_run_timeframe_job_skips_gap_repair_before_first_stored_candle(db_session):
    """A newly listed coin has no history to repair before its first candle."""
    hour = floor_to_timeframe(utc_now(), "1h")
    db_session.add(Symbol(symbol="NEWUSDT", base_asset="NEW", quote_asset="USDT", is_active=True))
    for offset in (2, 1, 0):
        db_session.add(_kline("NEWUSDT", "1h", hour - timedelta(hours=offset)))
    db_session.commit()
    fake_client = _FakeBinanceClient()

    run_timeframe_job(
        session_factory=lambda: db_session, binance_client=fake_client,
        timeframe="1h", now=hour + timedelta(minutes=5),
    )

    # Only the regular incremental fetch; no gap fetch for pre-listing history.
    assert len(fake_client.calls) == 1


def test_run_timeframe_job_logs_run_summary(db_session, caplog):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    with caplog.at_level(logging.INFO, logger="scheduler"):
        run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1h")

    assert any("1 symbols succeeded, 0 failed" in record.getMessage()
               for record in caplog.records if record.name == "scheduler")


def test_run_timeframe_job_logs_symbol_failures(db_session, caplog):
    db_session.add(Symbol(symbol="FAILSYMBOL", base_asset="FAIL", quote_asset="USDT", is_active=True))
    db_session.commit()

    with caplog.at_level(logging.ERROR, logger="scheduler"):
        run_timeframe_job(
            session_factory=lambda: db_session,
            binance_client=_PartiallyFailingBinanceClient(),
            timeframe="1h",
        )

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors, "a failing symbol must not fail silently"
    assert any("FAILSYMBOL" in record.getMessage() for record in errors)


def test_run_timeframe_job_logs_exception_when_no_fetch_log_row_can_be_written(db_session, caplog, monkeypatch):
    """A failure before record_run leaves no audit row — it must still be logged."""
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("watermark lookup exploded")

    monkeypatch.setattr("src.scheduler.get_resume_point", boom)

    with caplog.at_level(logging.ERROR, logger="scheduler"):
        run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1h")

    assert db_session.query(FetchLog).count() == 0
    assert any(record.exc_info for record in caplog.records if record.levelno >= logging.ERROR)


def test_run_symbol_refresh_job_logs_and_survives_failure(db_session, caplog):
    class _BrokenClient:
        def get_active_usdt_symbols(self):
            raise RuntimeError("binance down")

    with caplog.at_level(logging.ERROR, logger="scheduler"):
        run_symbol_refresh_job(session_factory=lambda: db_session, binance_client=_BrokenClient())

    assert any(record.exc_info for record in caplog.records if record.levelno >= logging.ERROR)
