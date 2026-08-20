from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.db.models import Symbol
from src.fetch_log import get_last_successful_run, record_run
from src.kline_fetcher import fetch_and_store
from src.symbol_registry import refresh_symbols


def _to_epoch_ms(naive_utc_dt: datetime) -> int:
    return int(naive_utc_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def run_timeframe_job(session_factory, binance_client, timeframe: str) -> None:
    session = session_factory()
    try:
        symbols = [row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()]  # noqa: E712
        end = datetime.now(timezone.utc).replace(tzinfo=None)
        for symbol in symbols:
            try:
                last_success = get_last_successful_run(session, symbol, timeframe)
                start = last_success if last_success else end - timedelta(days=730)
                started_at = datetime.now(timezone.utc).replace(tzinfo=None)
                result = fetch_and_store(
                    session, binance_client, symbol, timeframe,
                    start_ms=_to_epoch_ms(start),
                    end_ms=_to_epoch_ms(end),
                )
                if result.error:
                    # fetch_and_store may have failed mid-flush/commit (e.g. a
                    # DB constraint violation in the storage layer), which
                    # leaves the session dirty. Roll back unconditionally so
                    # record_run's own commit below starts from a clean
                    # session, regardless of what kind of error occurred.
                    session.rollback()
                finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                record_run(
                    session, symbol, timeframe,
                    status="error" if result.error else "success",
                    started_at=started_at, finished_at=finished_at,
                    error_message=result.error,
                )
            except Exception:
                # A single symbol's failure (including a failure in
                # record_run itself) must never abort processing of the
                # remaining symbols.
                session.rollback()
    finally:
        session.close()


def run_symbol_refresh_job(session_factory, binance_client) -> None:
    session = session_factory()
    try:
        refresh_symbols(session, binance_client)
    finally:
        session.close()


def build_scheduler(session_factory, binance_client) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        lambda: run_timeframe_job(session_factory, binance_client, "1h"),
        CronTrigger(minute=5),
        id="hourly_klines",
    )
    scheduler.add_job(
        lambda: run_timeframe_job(session_factory, binance_client, "1d"),
        CronTrigger(hour=0, minute=10),
        id="daily_klines",
    )
    scheduler.add_job(
        lambda: run_symbol_refresh_job(session_factory, binance_client),
        CronTrigger(hour=0, minute=0),
        id="symbol_refresh",
    )
    return scheduler
