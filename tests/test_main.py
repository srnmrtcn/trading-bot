import logging
import signal
from contextlib import ExitStack, contextmanager
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

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


def test_configure_logging_accepts_a_bare_filename(tmp_path, monkeypatch):
    """os.path.dirname('app.log') is '' and makedirs('') raises."""
    from logging.handlers import RotatingFileHandler

    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        main_module.configure_logging(log_file="app.log")
        assert (tmp_path / "app.log").exists()
    finally:
        for handler in root.handlers:
            if isinstance(handler, RotatingFileHandler):
                handler.close()
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_run_forever_starts_scheduler_serves_dashboard_and_shuts_down_on_exit(monkeypatch):
    calls = []

    class _FakeScheduler:
        def start(self):
            calls.append("scheduler.start")

        def shutdown(self, wait=True):
            calls.append(("scheduler.shutdown", wait))

    class _FakeApp:
        pass

    def _fake_serve(app, host, port, **kwargs):
        calls.append(("serve", host, port))
        raise KeyboardInterrupt()

    def _fake_get_basic_auth_credentials():
        calls.append("get_basic_auth_credentials")
        return ("admin", "hash")

    def _fake_create_app(session_factory, auth_user, auth_pass_hash):
        calls.append("create_app")
        return _FakeApp()

    def _fake_build_scheduler(session_factory, binance_client):
        calls.append("build_scheduler")
        return _FakeScheduler()

    monkeypatch.setattr(main_module, "build_scheduler", _fake_build_scheduler)
    monkeypatch.setattr(main_module, "get_basic_auth_credentials", _fake_get_basic_auth_credentials)
    monkeypatch.setattr(main_module, "create_app", _fake_create_app)
    monkeypatch.setattr(main_module, "serve", _fake_serve)
    monkeypatch.setenv("PORT", "9000")

    main_module.run_forever(session_factory=lambda: None, binance_client=None)

    # Config must be read and the app built BEFORE the scheduler starts, so a
    # missing env var or bad PORT never leaves the scheduler running unshut.
    assert calls == [
        "get_basic_auth_credentials",
        "create_app",
        "build_scheduler",
        "scheduler.start",
        ("serve", "0.0.0.0", 9000),
        # wait=True: a redeploy's SIGTERM must let the in-flight hourly job
        # finish its commit instead of dropping it mid-step.
        ("scheduler.shutdown", True),
    ]


def test_run_forever_defaults_to_port_8000_when_unset(monkeypatch):
    calls = []

    class _FakeScheduler:
        def start(self):
            pass

        def shutdown(self, wait=True):
            pass

    class _FakeApp:
        pass

    def _fake_serve(app, host, port, **kwargs):
        calls.append(("serve", host, port))
        raise KeyboardInterrupt()

    monkeypatch.setattr(main_module, "serve", _fake_serve)
    monkeypatch.setattr(main_module, "build_scheduler", lambda session_factory, binance_client: _FakeScheduler())
    monkeypatch.setattr(main_module, "get_basic_auth_credentials", lambda: ("admin", "hash"))
    monkeypatch.setattr(
        main_module, "create_app",
        lambda session_factory, auth_user, auth_pass_hash: _FakeApp(),
    )
    monkeypatch.delenv("PORT", raising=False)

    main_module.run_forever(session_factory=lambda: None, binance_client=None)

    assert calls == [("serve", "0.0.0.0", 8000)]


def test_sigterm_handler_raises_system_exit_so_the_finally_block_runs():
    # Python's default SIGTERM action ends the process without unwinding —
    # no `finally`, no scheduler.shutdown. Turning it into SystemExit reuses
    # the existing except/finally path that Ctrl+C already takes.
    with pytest.raises(SystemExit):
        main_module._raise_system_exit(signal.SIGTERM, None)


def test_run_forever_installs_the_sigterm_handler_before_serving(monkeypatch):
    installed = {}

    def _fake_signal(signum, handler):
        installed[signum] = handler

    class _FakeScheduler:
        def start(self):
            pass

        def shutdown(self, wait=True):
            pass

    class _FakeApp:
        pass

    def _fake_serve(app, host, port, **kwargs):
        raise SystemExit()

    monkeypatch.setattr(main_module, "serve", _fake_serve)
    monkeypatch.setattr(main_module.signal, "signal", _fake_signal)
    monkeypatch.setattr(main_module, "build_scheduler", lambda session_factory, binance_client: _FakeScheduler())
    monkeypatch.setattr(main_module, "get_basic_auth_credentials", lambda: ("admin", "hash"))
    monkeypatch.setattr(
        main_module, "create_app",
        lambda session_factory, auth_user, auth_pass_hash: _FakeApp(),
    )

    main_module.run_forever(session_factory=lambda: None, binance_client=None)

    assert installed[signal.SIGTERM] is main_module._raise_system_exit
