import logging
from contextlib import ExitStack, contextmanager
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import src.main as main_module
from src.db.models import Kline, Symbol
from src.kline_fetcher import FetchResult


def _kline(symbol, timeframe, open_time):
    return Kline(
        symbol=symbol, timeframe=timeframe, open_time=open_time,
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
        close=Decimal("1"), volume=Decimal("1"), flagged=False,
    )


@contextmanager
def _patched_startup(db_session, backfill_side_effect, refresh_side_effect=None):
    """Run startup() against the in-memory session with all I/O stubbed out."""
    with ExitStack() as stack:
        for patcher in (
            patch.object(main_module, "make_engine", return_value=db_session.get_bind()),
            patch.object(main_module, "create_all_tables", return_value=None),
            patch.object(main_module, "make_session_factory", return_value=lambda: db_session),
            patch.object(main_module, "BinanceClient", return_value=object()),
            patch.object(main_module, "refresh_symbols", side_effect=refresh_side_effect, return_value=None),
            patch.object(main_module, "run_initial_backfill", side_effect=backfill_side_effect),
        ):
            stack.enter_context(patcher)
        yield


def _record_backfill(calls):
    def _side_effect(session, client, symbols, timeframes, *args, **kwargs):
        calls.extend((symbol, timeframe) for symbol in symbols for timeframe in timeframes)
        return [
            FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)
            for symbol in symbols for timeframe in timeframes
        ]
    return _side_effect


def test_startup_runs_initial_backfill_when_no_klines_exist(db_session, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    calls = []
    with _patched_startup(db_session, _record_backfill(calls)):
        main_module.startup()

    assert set(calls) == {("BTCUSDT", "1h"), ("BTCUSDT", "1d")}


def test_startup_skips_initial_backfill_when_all_symbols_have_klines(db_session, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.add(_kline("BTCUSDT", "1h", datetime(2026, 1, 1)))
    db_session.add(_kline("BTCUSDT", "1d", datetime(2026, 1, 1)))
    db_session.commit()

    calls = []
    with _patched_startup(db_session, _record_backfill(calls)):
        main_module.startup()

    assert calls == []


def test_startup_backfills_only_symbols_missing_data(db_session, monkeypatch):
    """A crash partway through the initial backfill must not leave the
    remaining symbols without history on restart."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.add(Symbol(symbol="ETHUSDT", base_asset="ETH", quote_asset="USDT", is_active=True))
    # BTCUSDT finished 1h but not 1d; ETHUSDT never started.
    db_session.add(_kline("BTCUSDT", "1h", datetime(2026, 1, 1)))
    db_session.commit()

    calls = []
    with _patched_startup(db_session, _record_backfill(calls)):
        main_module.startup()

    assert set(calls) == {("BTCUSDT", "1d"), ("ETHUSDT", "1h"), ("ETHUSDT", "1d")}


def test_startup_survives_symbol_refresh_failure(db_session, monkeypatch, caplog):
    """A transient boot error must not kill an unattended service."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    calls = []
    with caplog.at_level(logging.ERROR, logger="main"), _patched_startup(
        db_session, _record_backfill(calls), refresh_side_effect=RuntimeError("binance unreachable"),
    ):
        session_factory, binance_client = main_module.startup()

    assert session_factory is not None
    assert calls == []  # no active symbols known yet -> nothing to backfill
    assert any(record.exc_info for record in caplog.records if record.levelno >= logging.ERROR)


def test_startup_logs_initial_backfill_failures(db_session, monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    def failing_backfill(session, client, symbols, timeframes, *args, **kwargs):
        return [
            FetchResult(symbol=symbols[0], timeframe=timeframes[0], fetched=0, inserted=0,
                        updated=0, flagged=0, error="network down")
        ]

    with caplog.at_level(logging.INFO, logger="main"), _patched_startup(db_session, failing_backfill):
        main_module.startup()

    messages = [record.getMessage() for record in caplog.records if record.name == "main"]
    assert any("network down" in message for message in messages)
    assert any("0 succeeded" in message for message in messages)


def test_configure_logging_adds_console_and_rotating_file_handlers(tmp_path):
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    log_file = tmp_path / "logs" / "app.log"
    try:
        main_module.configure_logging(log_file=str(log_file))
        handler_types = [type(handler) for handler in root.handlers]
        assert RotatingFileHandler in handler_types
        assert any(
            issubclass(handler_type, logging.StreamHandler) and not issubclass(handler_type, RotatingFileHandler)
            for handler_type in handler_types
        )
        logging.getLogger("main").info("hello file handler")
        for handler in root.handlers:
            handler.flush()
        assert "hello file handler" in log_file.read_text()
    finally:
        for handler in root.handlers:
            if isinstance(handler, RotatingFileHandler):
                handler.close()
        root.handlers = original_handlers
        root.setLevel(original_level)
